"""Normalisation and the two "good" terms of the score: N_i (network) and C_i (cost).

Every score here is in [0, 1] and HIGHER IS BETTER.
"""
from __future__ import annotations

from rmc.models import ProbeResult


def min_max(values: dict[str, float], higher_is_better: bool) -> dict[str, float]:
    """Scale values across providers to [0, 1]: best -> 1, worst -> 0.
    If all values are equal, every provider gets 1.0 (no provider is worse)."""
    lo, hi = min(values.values()), max(values.values())
    if hi == lo:
        return {k: 1.0 for k in values}
    if higher_is_better:
        return {k: (v - lo) / (hi - lo) for k, v in values.items()}
    return {k: (hi - v) / (hi - lo) for k, v in values.items()}


def reference(value: float, lo: float, hi: float, higher_is_better: bool) -> float:
    """Scale one value against a fixed range [lo, hi], clipped to [0, 1]."""
    x = (value - lo) / (hi - lo)
    x = min(1.0, max(0.0, x))
    return x if higher_is_better else 1.0 - x


# (probe field, config key in network_score.reference, higher is better)
_METRICS = {
    "rtt": ("rtt_ms", "rtt_ms", False),
    "throughput": ("throughput_mbps", "throughput_mbps", True),
    "loss": ("loss_pct", "loss_pct", False),
}


def network_scores(probes: dict[str, ProbeResult], ns_cfg: dict) -> dict[str, float]:
    """N_i for every provider: weighted sum of normalised RTT, throughput and loss.

    A failed probe, or a missing metric, scores 0 on that metric (treated as the worst)
    and is left out when computing the min-max range of the other providers.
    ns_cfg is cfg["network_score"].
    """
    method = ns_cfg["normalisation"]
    totals = {p: 0.0 for p in probes}
    for metric, weight in ns_cfg["components"].items():
        field, ref_key, higher = _METRICS[metric]
        raw = {p: getattr(r, field) for p, r in probes.items() if r.ok and getattr(r, field) is not None}
        if not raw:
            continue  # nobody has this metric: it adds 0 for everyone
        if method == "min_max":
            norm = min_max(raw, higher)
        elif method == "reference":
            lo, hi = ns_cfg["reference"][ref_key]
            norm = {p: reference(v, lo, hi, higher) for p, v in raw.items()}
        else:
            raise ValueError(f"unknown network_score.normalisation {method!r}")
        for p in probes:
            totals[p] += weight * norm.get(p, 0.0)
    return totals


def cost_per_transfer_usd(provider: str, cost_cfg: dict, size_bytes: int) -> float:
    """Published-pricing cost of storing (and egressing) one file. cost_cfg is cfg["cost"]."""
    price = cost_cfg["table"][provider]
    size_gb = size_bytes / 1e9   # providers bill in decimal GB
    return size_gb * (price["storage_usd_per_gb_month"] * cost_cfg["storage_months"]
                      + price["egress_usd_per_gb"] * cost_cfg["egress_fraction"])


def cost_scores(providers: list[str], cost_cfg: dict, size_bytes: int) -> dict[str, float]:
    """C_i for every provider: higher for cheaper."""
    costs = {p: cost_per_transfer_usd(p, cost_cfg, size_bytes) for p in providers}
    method = cost_cfg["normalisation"]
    if method == "min_max":
        return min_max(costs, higher_is_better=False)
    if method == "ratio":
        cheapest = min(costs.values())
        return {p: (cheapest / c if c > 0 else 1.0) for p, c in costs.items()}
    raise ValueError(f"unknown cost.normalisation {method!r}")
