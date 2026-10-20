"""Network probes: RTT, packet loss and throughput for each provider, before each decision.

The probes measure the simulated providers (MockProvider.probe). With the config's
probe section, a probe is costed like a real one would be:
  rtt / loss   `probe.samples` TCP-connect samples, one RTT each;
               rtt = median, loss = lost samples / samples (coarse for few samples)
  throughput   a small upload of `probe.throughput_bytes` (3 RTTs set-up + transfer);
               it includes connection set-up, so it is meant for comparing providers,
               not as a true bandwidth figure.
"""
from __future__ import annotations

from statistics import median

from rmc.models import ProbeResult


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
    per provider, with no degradation. Used by the weight rule (RTT increase)
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
