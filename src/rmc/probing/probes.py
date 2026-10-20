"""Network probes: RTT, packet loss and throughput for each provider, before each decision.

RTT and loss (one batch of `probe.samples` attempts to the provider's endpoint):
  tcp_connect  time a TCP handshake to endpoint_host:port. Works where ICMP is blocked.
               A connect slower than sample_timeout_s counts as LOST: a lost SYN or
               SYN-ACK is only retransmitted after ~1 s, so it shows up as a slow connect.
  icmp         ping3 echo requests. Often blocked by cloud endpoints, and on Linux it
               needs root or net.ipv4.ping_group_range. TEST ON THE REAL MACHINE.
  rtt  = median of the successful samples (ms); loss = lost / samples * 100.
  With 10 samples the loss estimate moves in steps of 10 %, so it can only tell
  "no loss" from "heavy loss" - it cannot measure 1 % loss. Treat it as coarse.

Throughput: time an upload of a small file (512 KiB by default) to the provider,
always to the same per-machine key (<object_key_prefix>/<machine id>.bin), which it
overwrites, so probes do not pile up and two machines never write the same key.
Includes connection set-up, so it underestimates bulk throughput; it is meant for comparing providers, not as a
true bandwidth figure.

The endpoint's IP is resolved once per probe, outside the timed part.
"""
from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from statistics import median

from rmc.config import REPO_ROOT
from rmc.models import LocalFile, ProbeResult
from rmc.providers.base import StorageProvider
from rmc.runinfo import machine_id


def resolve_ipv4(host: str, port: int) -> str:
    return socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]


def tcp_connect_ms(ip: str, port: int, timeout_s: float) -> float | None:
    """One TCP handshake time in ms, or None if it failed or took longer than timeout_s."""
    start = time.perf_counter()
    try:
        with socket.create_connection((ip, port), timeout=timeout_s):
            pass
    except OSError:
        return None
    return (time.perf_counter() - start) * 1000


def icmp_ms(host: str, timeout_s: float) -> float | None:
    import ping3
    r = ping3.ping(host, timeout=timeout_s, unit="ms")
    return r if isinstance(r, float) else None   # ping3 returns None (timeout) or False (error)


def summarise(samples: list[float | None]) -> tuple[float | None, float]:
    """(median RTT of successful samples or None, loss %)."""
    ok = [s for s in samples if s is not None]
    loss = 100.0 * (len(samples) - len(ok)) / len(samples)
    return (median(ok) if ok else None), loss


class NetworkProber:
    """Real probes. `hosts` overrides endpoint_host per provider (used in tests)."""

    def __init__(self, cfg: dict, providers: dict[str, StorageProvider],
                 probe_file: LocalFile | None = None, hosts: dict[str, str] | None = None):
        self.pcfg = cfg["probe"]
        self.providers = providers
        self.hosts = hosts or {n: p["endpoint_host"] for n, p in cfg["providers"].items()}
        self.probe_key = f"{self.pcfg['object_key_prefix']}/{machine_id()}.bin"
        self.probe_file = probe_file or LocalFile.from_path(REPO_ROOT / self.pcfg["payload_path"])
        if self.pcfg["throughput_method"] != "small_upload":
            raise NotImplementedError(f"throughput_method {self.pcfg['throughput_method']!r}")

    def _rtt_loss(self, name: str) -> tuple[float | None, float]:
        c = self.pcfg
        host = self.hosts[name]
        if c["rtt_method"] == "tcp_connect":
            ip = resolve_ipv4(host, c["port"])
            samples = [tcp_connect_ms(ip, c["port"], c["sample_timeout_s"]) for _ in range(c["samples"])]
        elif c["rtt_method"] == "icmp":
            samples = [icmp_ms(host, c["sample_timeout_s"]) for _ in range(c["samples"])]
        else:
            raise ValueError(f"unknown probe.rtt_method {c['rtt_method']!r}")
        return summarise(samples)

    def _throughput_mbps(self, name: str) -> float:
        provider = self.providers[name]
        start = time.perf_counter()
        provider.put_with_deadline(self.probe_file, self.probe_key, self.pcfg["timeout_s"], overwrite=True)
        elapsed = max(time.perf_counter() - start, 1e-9)
        return self.probe_file.size_bytes * 8 / elapsed / 1e6

    def probe(self, name: str) -> ProbeResult:
        start = time.perf_counter()
        rtt = loss = thr = None
        errors = []
        try:
            rtt, loss = self._rtt_loss(name)
            if rtt is None:
                errors.append("all RTT samples lost")
        except Exception as e:
            errors.append(f"rtt: {type(e).__name__}: {e}")
        try:
            thr = self._throughput_mbps(name)
        except Exception as e:
            errors.append(f"throughput: {type(e).__name__}: {e}")
        return ProbeResult(provider=name, rtt_ms=rtt, throughput_mbps=thr, loss_pct=loss,
                           probe_time_s=time.perf_counter() - start, ok=not errors,
                           error="; ".join(errors) or None)


class MockProber:
    """Probes the simulated network of MockProvider objects. With `pcfg` (the config's
    probe section) probes are costed like the real ones (samples + small upload)."""

    def __init__(self, mocks: dict, timeout_s: float = 10.0, pcfg: dict | None = None):
        self.mocks = mocks
        self.timeout_s = timeout_s
        self.pcfg = pcfg

    def probe(self, name: str) -> ProbeResult:
        if self.pcfg is None:
            return self.mocks[name].probe(self.timeout_s)
        return self.mocks[name].probe(self.timeout_s, samples=self.pcfg["samples"],
                                      probe_bytes=self.pcfg["throughput_bytes"])


def probe_all(prober, names: list[str]) -> dict[str, ProbeResult]:
    """Probe every provider, one after another (sequential, so probes do not compete
    for bandwidth). Total probing time = sum of the probe_time_s values."""
    return {n: prober.probe(n) for n in names}


def calibrate(prober, names: list[str], samples: int) -> dict[str, dict]:
    """Natural-network baseline: median RTT / loss / throughput over `samples` probes
    per provider. Run with NO netem rules. Used by the weight rule (RTT increase)
    and by nearest_order(method=min_median_rtt)."""
    results: dict[str, list[ProbeResult]] = {n: [] for n in names}
    for _ in range(samples):
        for n in names:   # interleave providers so a temporary blip affects all of them
            results[n].append(prober.probe(n))
    baseline = {}
    for n, rs in results.items():
        ok = [r for r in rs if r.ok]
        if not ok:
            raise RuntimeError(f"calibration: every probe of {n} failed; last error: {rs[-1].error}")
        baseline[n] = {"rtt_ms": median(r.rtt_ms for r in ok),
                       "loss_pct": median(r.loss_pct for r in ok),
                       "throughput_mbps": median(r.throughput_mbps for r in ok),
                       "n_ok": len(ok), "n": len(rs)}
    return baseline


def save_baseline(path: str | Path, baseline: dict) -> None:
    """Write the baseline once. Refuses to overwrite: delete it deliberately to recalibrate."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} exists; refusing to overwrite the calibration")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
