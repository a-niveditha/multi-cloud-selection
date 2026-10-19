"""Apply / clear tc-netem impairment on ONE provider's destination path (Linux, sudo).

How it works (egress only, on `netem.interface`):

    tc qdisc add dev IF root handle 1: prio bands 4
    tc qdisc add dev IF parent 1:4 handle 40: netem delay <D>ms loss <L>% limit 10000
    tc filter add dev IF parent 1: protocol ip prio 1 u32 match ip dst <CIDR> flowid 1:4   (one per CIDR)

The default prio priomap only uses bands 1-3, so ordinary traffic never enters
band 4; only packets to the degraded provider's prefixes are delayed/dropped.
Delay is added once on egress, so the RTT grows by D. D = rtt_increase_ratio x
the provider's natural median RTT from calibration. Loss on egress drops our
upload data packets (incoming ACKs are not affected).

    tc qdisc del dev IF root      # clear (removes everything above)

UNVERIFIED - TEST ON THE TRIAL MACHINE BEFORE ANY REAL BLOCK:
  * Only IPv4 is filtered. If the machine reaches a provider over IPv6, the
    traffic bypasses netem. Check with `ip -6 route` / disable IPv6 for the trials.
  * The `targets` CIDRs must cover the IPs the SDKs actually connect to. DNS can
    change; the runner checks the endpoint's current IPv4 addresses before a block.
  * Commands use `sudo -n` (non-interactive): run `sudo -v` first, or the
    command fails instead of hanging on a password prompt.
  * Check the result with `tc -s qdisc show dev IF` and `tc filter show dev IF`.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import subprocess

PRIO_HANDLE = "1:"
NETEM_BAND = "1:4"


def netem_interface(cfg: dict) -> str:
    """The interface differs per machine, so RMC_NETEM_INTERFACE (in .env) overrides the
    config value without changing config.yaml (whose hash must match on every machine)."""
    return os.environ.get("RMC_NETEM_INTERFACE") or cfg["netem"]["interface"]


class TcNetem:
    def __init__(self, interface: str, targets: dict[str, list[str]], baseline_rtt_ms: dict[str, float],
                 dry_run: bool = True, sudo: bool = True, run=subprocess.run, echo=print):
        self.interface = interface
        self.targets = targets
        self.baseline_rtt_ms = baseline_rtt_ms
        self.dry_run = dry_run
        self.prefix = ["sudo", "-n"] if sudo else []
        self._run = run
        self._echo = echo
        self.log: list[list[str]] = []          # every command, executed or not

    # ---- low level ----------------------------------------------------------
    def _cmd(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess | None:
        cmd = self.prefix + args
        self.log.append(cmd)
        if self.dry_run:
            self._echo("[dry-run] " + " ".join(cmd))
            return None
        return self._run(cmd, capture_output=True, text=True, check=check)

    # ---- commands -----------------------------------------------------------
    def apply_commands(self, provider: str, rtt_increase_ratio: float, loss_pct: float) -> list[list[str]]:
        cidrs = self.targets.get(provider) or []
        if not cidrs:
            raise ValueError(f"netem.targets.{provider} is empty; refusing to apply netem to nothing")
        delay_ms = round(rtt_increase_ratio * self.baseline_rtt_ms[provider], 1)
        dev = ["dev", self.interface]
        cmds = [
            ["tc", "qdisc", "add", *dev, "root", "handle", PRIO_HANDLE, "prio", "bands", "4"],
            ["tc", "qdisc", "add", *dev, "parent", NETEM_BAND, "handle", "40:", "netem",
             "delay", f"{delay_ms}ms", "loss", f"{loss_pct}%", "limit", "10000"],
        ]
        for cidr in cidrs:
            cmds.append(["tc", "filter", "add", *dev, "parent", PRIO_HANDLE, "protocol", "ip", "prio", "1",
                         "u32", "match", "ip", "dst", cidr, "flowid", NETEM_BAND])
        return cmds

    def apply(self, provider: str, rtt_increase_ratio: float, loss_pct: float) -> None:
        for cmd in self.apply_commands(provider, rtt_increase_ratio, loss_pct):
            self._cmd(cmd)

    def clear(self) -> None:
        """Remove our root qdisc. 'No such file' / 'handle of zero' errors just mean nothing was there."""
        r = self._cmd(["tc", "qdisc", "del", "dev", self.interface, "root"], check=False)
        if r is not None and r.returncode != 0:
            err = (r.stderr or "").lower()
            if "no such file" not in err and "handle of zero" not in err and "cannot find" not in err:
                raise RuntimeError(f"tc qdisc del failed: {r.stderr.strip()}")

    def show(self) -> str:
        if self.dry_run:
            self._echo(f"[dry-run] tc qdisc show dev {self.interface}")
            return ""
        r = self._run(["tc", "qdisc", "show", "dev", self.interface], capture_output=True, text=True, check=True)
        return r.stdout

    def has_rules(self) -> bool:
        """True if a netem qdisc (ours or anyone's) is on the interface. Always False in dry-run."""
        return "netem" in self.show()


class MockNetem:
    """Same interface, but degrades MockProvider objects instead of the real network."""

    def __init__(self, mocks: dict):
        self.mocks = mocks
        self.log: list[tuple] = []

    def apply(self, provider: str, rtt_increase_ratio: float, loss_pct: float) -> None:
        self.log.append(("apply", provider, rtt_increase_ratio, loss_pct))
        self.mocks[provider].degrade(rtt_increase_ratio, loss_pct)

    def clear(self) -> None:
        self.log.append(("clear",))
        for m in self.mocks.values():
            m.clear_degradation()

    def has_rules(self) -> bool:
        return any(m.rtt_ms != m.base.rtt_ms or m.loss_pct != m.base.loss_pct for m in self.mocks.values())


def uncovered_endpoint_ips(host: str, cidrs: list[str], port: int = 443) -> list[str]:
    """Current IPv4 addresses of `host` that are NOT inside any of `cidrs` (should be empty)."""
    nets = [ipaddress.ip_network(c, strict=False) for c in cidrs]
    ips = {ai[4][0] for ai in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)}
    return sorted(ip for ip in ips if not any(ipaddress.ip_address(ip) in n for n in nets))
