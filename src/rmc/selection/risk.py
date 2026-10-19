"""Risk penalty rho_i in [0, 1]: observed UNRELIABILITY of a provider (not security).

Built from the provider's K most recent attempts and K most recent probes:
  timeout_rate      timed-out attempts / attempts
  failure_rate      other failed attempts (errors, checksum mismatch) / attempts
  transfer_time_cv  coefficient of variation of successful transfer times
  loss_std          standard deviation of probe loss (%)
  rtt_cv            coefficient of variation of probe RTT
Unbounded components are divided by a cap and clipped to [0, 1]. rho is the
weighted sum of the components (weights from cfg["risk"]["components"], summing to 1).
A component with no data counts as 0. A provider with no history at all gets
cfg["risk"]["cold_start_rho"].

All of this (window, components, weights, caps) is a TODO in config.yaml.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from statistics import mean, pstdev

from rmc.models import AttemptResult, ProbeResult


class HistoryStore:
    """Rolling per-provider history: the last K attempts and the last K probes.
    The caller decides what goes in (history_source = own / all) and when to reset
    (history_scope = block)."""

    def __init__(self, window_k: int):
        self.window_k = window_k
        self._attempts: dict[str, deque[AttemptResult]] = defaultdict(lambda: deque(maxlen=window_k))
        self._probes: dict[str, deque[ProbeResult]] = defaultdict(lambda: deque(maxlen=window_k))

    def add_attempt(self, a: AttemptResult) -> None:
        self._attempts[a.provider].append(a)

    def add_probe(self, p: ProbeResult) -> None:
        self._probes[p.provider].append(p)

    def attempts(self, provider: str) -> list[AttemptResult]:
        return list(self._attempts[provider])

    def probes(self, provider: str) -> list[ProbeResult]:
        return list(self._probes[provider])

    def reset(self) -> None:
        self._attempts.clear()
        self._probes.clear()


@dataclass(frozen=True)
class RiskBreakdown:
    rho: float
    components: dict[str, float]    # each already in [0, 1]
    n_attempts: int
    n_probes: int


def _cv(values: list[float]) -> float:
    """Coefficient of variation (population std / mean); 0 with fewer than 2 values."""
    if len(values) < 2 or mean(values) <= 0:
        return 0.0
    return pstdev(values) / mean(values)


def _capped(x: float, cap: float) -> float:
    return min(1.0, max(0.0, x / cap))


def risk_score(attempts: list[AttemptResult], probes: list[ProbeResult], risk_cfg: dict) -> RiskBreakdown:
    """rho for one provider. risk_cfg is cfg["risk"]."""
    if not attempts and not probes:
        return RiskBreakdown(risk_cfg["cold_start_rho"], {}, 0, 0)

    caps = risk_cfg["caps"]
    n = len(attempts)
    ok_times = [a.transfer_time_s for a in attempts if a.ok]
    losses = [p.loss_pct for p in probes if p.ok and p.loss_pct is not None]
    rtts = [p.rtt_ms for p in probes if p.ok and p.rtt_ms is not None]

    comp = {
        "timeout_rate": sum(a.timed_out for a in attempts) / n if n else 0.0,
        "failure_rate": sum(1 for a in attempts if not a.ok and not a.timed_out) / n if n else 0.0,
        "transfer_time_cv": _capped(_cv(ok_times), caps["transfer_time_cv"]),
        "loss_std": _capped(pstdev(losses), caps["loss_std_pct"]) if len(losses) >= 2 else 0.0,
        "rtt_cv": _capped(_cv(rtts), caps["rtt_cv"]),
    }
    weights = risk_cfg["components"]
    rho = sum(weights[k] * comp[k] for k in weights)
    return RiskBreakdown(min(1.0, max(0.0, rho)), comp, n, len(probes))


def risk_scores(providers: list[str], history: HistoryStore, risk_cfg: dict) -> dict[str, RiskBreakdown]:
    return {p: risk_score(history.attempts(p), history.probes(p), risk_cfg) for p in providers}
