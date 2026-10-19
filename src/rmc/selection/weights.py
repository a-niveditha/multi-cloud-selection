"""Adaptive weights w_N(t), w_C(t), w_R(t).

PLACEHOLDER. The real weight-adaptation rule is NOT decided yet. The rule is
pluggable: write a function with the signature below, decorate it with
@register("name"), and set `weights.rule: name` in config.yaml.

    rule(probes, baseline, cfg) -> (weights, profile_name)
      probes:   {provider: ProbeResult} measured just before this decision
      baseline: {provider: {"rtt_ms": ..., "loss_pct": ...}} from the natural
                calibration, or None if calibration has not been run
      weights:  {"network": w_N, "cost": w_C, "risk": w_R}
"""
from __future__ import annotations

from typing import Callable

from rmc.models import ProbeResult

WeightRule = Callable[[dict[str, ProbeResult], "dict | None", dict], "tuple[dict[str, float], str]"]
WEIGHT_RULES: dict[str, WeightRule] = {}


def register(name: str):
    def deco(fn: WeightRule) -> WeightRule:
        WEIGHT_RULES[name] = fn
        return fn
    return deco


def get_rule(name: str) -> WeightRule:
    if name not in WEIGHT_RULES:
        raise ValueError(f"unknown weights.rule {name!r}; known: {sorted(WEIGHT_RULES)}")
    return WEIGHT_RULES[name]


def is_degraded(probes: dict[str, ProbeResult], baseline: dict | None, indicator: dict) -> bool:
    """Degradation indicator: True if ANY provider's live probe shows
      * a failed probe, or
      * loss_pct >= indicator["loss_pct"], or
      * rtt_ms >= baseline rtt * (1 + indicator["rtt_increase_ratio"])  (only if a baseline exists).
    netem degrades one provider at a time, so "any provider" is what can trigger it."""
    for name, p in probes.items():
        if not p.ok:
            return True
        if p.loss_pct is not None and p.loss_pct >= indicator["loss_pct"]:
            return True
        base = (baseline or {}).get(name)
        if base and p.rtt_ms is not None and p.rtt_ms >= base["rtt_ms"] * (1 + indicator["rtt_increase_ratio"]):
            return True
    return False


@register("two_profile")
def two_profile(probes: dict[str, ProbeResult], baseline: dict | None, cfg: dict) -> tuple[dict[str, float], str]:
    """PLACEHOLDER rule: pick the "severe" weight profile if the network looks
    degraded, otherwise the "normal" profile. Values come from config."""
    wcfg = cfg["weights"]
    profile = "severe" if is_degraded(probes, baseline, wcfg["degradation_indicator"]) else "normal"
    return dict(wcfg["profiles"][profile]), profile
