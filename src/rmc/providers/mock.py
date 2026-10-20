"""Mock provider: simulates uploads, probes, failures, timeouts and netem-like
degradation, so the whole pipeline can be tested without cloud accounts.

The performance model is a deliberately simple TOY, not a TCP model:
    effective_throughput = throughput * (1 - min(0.9, LOSS_SENSITIVITY * loss_fraction))
    transfer_time        = (3 * RTT  +  size / effective_throughput) * jitter
where jitter is log-normal with the configured coefficient of variation and
3 * RTT stands for TCP + TLS + request set-up. All parameters are made up.

Two optional effects (both off by default) make the simulation less idealised:
  - loss_fail_per_pct: each 1 % of current packet loss adds this much to both the
    failure and the timeout probability (degraded paths fail more often);
  - congestion episodes: a two-state on/off process in simulated time. A provider
    enters an episode at `episode_rate_per_hour` and stays in it for an
    exponentially distributed time with mean `episode_mean_s`. During an episode
    its throughput is divided by `episode_slowdown` and `episode_extra_fail` is
    added to its failure probability. Probes see the slowdown too.

With a SimClock (default) nothing actually sleeps; with a RealClock the mock
sleeps for the simulated time (useful for a realistic dry run of the runner).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from rmc.clock import SimClock
from rmc.models import LocalFile, ProbeResult
from rmc.providers.base import StorageProvider

LOSS_SENSITIVITY = 10.0   # 1 % loss -> 10 % slower, 5 % loss -> 50 % slower (toy assumption)


@dataclass
class MockNetwork:
    rtt_ms: float
    throughput_mbps: float
    loss_pct: float
    failure_prob: float = 0.0     # chance an upload fails with an error
    timeout_prob: float = 0.0     # chance an upload hangs until the timeout
    jitter_cv: float = 0.0        # variability of transfer and probe times
    loss_fail_per_pct: float = 0.0    # extra failure and timeout probability per 1 % loss
    episode_rate_per_hour: float = 0.0
    episode_mean_s: float = 60.0
    episode_slowdown: float = 1.0
    episode_extra_fail: float = 0.0


class MockProvider(StorageProvider):
    def __init__(self, name: str, network: MockNetwork, seed: int = 0, clock=None):
        super().__init__(name, clock or SimClock())
        self.base = network
        self.rng = random.Random(seed)
        self._stored: dict[str, str] = {}          # key -> md5 of what was "uploaded"
        self._rtt_factor = 1.0
        self._extra_loss_pct = 0.0
        self._scripted: list[str] = []              # forced outcomes: "ok" | "fail" | "timeout" | "corrupt"
        self.in_episode = False
        self._episode_checked_at = self.clock.now()

    @classmethod
    def from_config(cls, cfg: dict, clock=None, section: str = "mock",
                    seed: int | None = None) -> dict[str, "MockProvider"]:
        """Build one mock per provider from cfg[section] ('mock' for tests, 'simulation'
        for simulation runs). Shares one clock across providers."""
        clock = clock or SimClock()
        seed = cfg[section]["seed"] if seed is None else seed
        return {
            name: cls(name, MockNetwork(**params), seed=seed + i, clock=clock)
            for i, (name, params) in enumerate(cfg[section]["providers"].items())
        }

    # ---- netem-like degradation ---------------------------------------------
    def degrade(self, rtt_increase_ratio: float, loss_pct: float) -> None:
        """Simulate netem on this provider's path, e.g. (0.25, 1.0) for 'mild'."""
        self._rtt_factor = 1.0 + rtt_increase_ratio
        self._extra_loss_pct = loss_pct

    def clear_degradation(self) -> None:
        self._rtt_factor, self._extra_loss_pct = 1.0, 0.0

    @property
    def rtt_ms(self) -> float:
        return self.base.rtt_ms * self._rtt_factor

    @property
    def loss_pct(self) -> float:
        return min(100.0, self.base.loss_pct + self._extra_loss_pct)

    # ---- deterministic test hooks -------------------------------------------
    def script(self, *outcomes: str) -> None:
        """Force the next upload outcomes, e.g. mock.script('timeout', 'ok')."""
        for o in outcomes:
            if o not in {"ok", "fail", "timeout", "corrupt"}:
                raise ValueError(o)
        self._scripted.extend(outcomes)

    # ---- simulation ---------------------------------------------------------
    def _update_episode(self) -> None:
        """Advance the on/off congestion process to the current simulated time."""
        if self.base.episode_rate_per_hour <= 0:
            return
        now = self.clock.now()
        dt, self._episode_checked_at = now - self._episode_checked_at, now
        if dt <= 0:
            return
        if self.in_episode:
            p_change = 1 - math.exp(-dt / self.base.episode_mean_s)
        else:
            p_change = 1 - math.exp(-dt * self.base.episode_rate_per_hour / 3600)
        if self.rng.random() < p_change:
            self.in_episode = not self.in_episode

    @property
    def throughput_mbps(self) -> float:
        slowdown = self.base.episode_slowdown if self.in_episode else 1.0
        return self.base.throughput_mbps / slowdown

    def _jitter(self) -> float:
        cv = self.base.jitter_cv
        if cv <= 0:
            return 1.0
        sigma = math.sqrt(math.log(1 + cv * cv))
        return self.rng.lognormvariate(-sigma * sigma / 2, sigma)   # mean 1

    def expected_transfer_time_s(self, size_bytes: int) -> float:
        """Transfer time without jitter (for tests and sanity checks)."""
        eff_mbps = self.throughput_mbps * (1 - min(0.9, LOSS_SENSITIVITY * self.loss_pct / 100))
        return 3 * self.rtt_ms / 1000 + size_bytes * 8 / (eff_mbps * 1e6)

    def put_object(self, file: LocalFile, key: str, timeout_s: float, overwrite: bool = False) -> None:
        if not overwrite and key in self._stored:   # strictest provider behaviour (GCS precondition)
            raise FileExistsError(f"mock {self.name}: {key} exists and overwrite=False")
        self._update_episode()
        if self._scripted:
            outcome = self._scripted.pop(0)
        else:
            from_loss = self.base.loss_fail_per_pct * self.loss_pct
            timeout_p = self.base.timeout_prob + from_loss
            failure_p = self.base.failure_prob + from_loss + (self.base.episode_extra_fail if self.in_episode else 0.0)
            r = self.rng.random()
            if r < timeout_p:
                outcome = "timeout"
            elif r < timeout_p + failure_p:
                outcome = "fail"
            else:
                outcome = "ok"

        duration = self.expected_transfer_time_s(file.size_bytes) * self._jitter()
        if outcome == "timeout" or duration > timeout_s:
            self.clock.sleep(timeout_s)
            raise TimeoutError(f"mock {self.name}: no completion within {timeout_s}s")
        if outcome == "fail":
            self.clock.sleep(duration * self.rng.random())        # fails part-way through
            raise ConnectionError(f"mock {self.name}: simulated upload failure")
        self.clock.sleep(duration)
        self._stored[key] = "corrupted" if outcome == "corrupt" else file.md5_hex

    def delete_object(self, key: str) -> None:
        self._stored.pop(key, None)

    def verify(self, file: LocalFile, key: str) -> bool:
        self.clock.sleep(self.rtt_ms / 1000)                       # one metadata request
        return self._stored.get(key) == file.md5_hex

    def probe(self, timeout_s: float = 10.0, samples: int | None = None,
              probe_bytes: int | None = None) -> ProbeResult:
        """Simulated probe (the real probes live in rmc.probing).

        Without `samples`: a toy probe costing 8 RTTs, with a smooth loss estimate.
        With `samples` and `probe_bytes`: costed like the real probe - `samples` TCP
        connects (one RTT each) plus a small upload of `probe_bytes` (3 RTTs set-up
        + transfer) - and loss is estimated the way the real probe does, as the
        fraction of the `samples` connects that were lost (coarse for few samples)."""
        self._update_episode()
        rtt = self.rtt_ms * self._jitter()
        if samples is None:
            loss = max(0.0, self.loss_pct * self._jitter())
        else:
            lost = sum(self.rng.random() < self.loss_pct / 100 for _ in range(samples))
            loss = 100.0 * lost / samples
        if samples is None:
            thr = self.throughput_mbps * (1 - min(0.9, LOSS_SENSITIVITY * loss / 100)) * self._jitter()
            probe_time = min(timeout_s, 8 * rtt / 1000)
        else:
            thr = self.throughput_mbps * (1 - min(0.9, LOSS_SENSITIVITY * self.loss_pct / 100)) * self._jitter()
            upload_s = 3 * rtt / 1000 + probe_bytes * 8 / (thr * 1e6)
            probe_time = samples * rtt / 1000 + min(timeout_s, upload_s)
        self.clock.sleep(probe_time)
        return ProbeResult(provider=self.name, rtt_ms=rtt, throughput_mbps=thr, loss_pct=loss,
                           probe_time_s=probe_time)
