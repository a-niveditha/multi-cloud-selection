"""The five provider-selection strategies.

Every strategy returns a Decision whose `ranking` lists ALL providers, best first.
The transfer layer uploads to ranking[0] and, on timeout/failure, fails over to
ranking[1], then ranking[2]. Ties are broken by the provider order in config.yaml.

  proposed        Score_i = w_N*N_i + w_C*C_i - w_R*rho_i, adaptive weights, risk history
  static_network  Score_i = N_i (live probes only, no cost, no risk history)
  cost_only       cheapest first (published pricing)
  nearest         fixed order decided ONCE at setup (closest first)
  random          uniform random order, reproducible from (seed, round key)
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from rmc.models import Decision, ProbeResult, Strategy
from rmc.selection.normalise import cost_per_transfer_usd, cost_scores, network_scores
from rmc.selection.risk import HistoryStore, risk_scores
from rmc.selection.weights import get_rule


@dataclass
class SelectionInput:
    providers: list[str]                            # in config order (used for tie-breaks)
    probes: dict[str, ProbeResult] | None = None    # required by network-aware strategies
    history: HistoryStore | None = None             # required by proposed
    baseline: dict | None = None                    # natural calibration, for the weight rule
    round_key: str = ""                             # e.g. "B03:R5"; seeds the random strategy


def rank(scores: dict[str, float], providers: list[str]) -> tuple[str, ...]:
    """Providers sorted by score (highest first); ties keep config order."""
    return tuple(sorted(providers, key=lambda p: (-scores[p], providers.index(p))))


def _require_probes(inp: SelectionInput, who: str) -> dict[str, ProbeResult]:
    if inp.probes is None or set(inp.probes) != set(inp.providers):
        raise ValueError(f"{who} needs a probe result for every provider")
    return inp.probes


class ProposedStrategy:
    name = Strategy.PROPOSED
    needs_probes = True

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.rule = get_rule(cfg["weights"]["rule"])
        self.size_bytes = cfg["experiment"]["payload_size_bytes"]

    def select(self, inp: SelectionInput) -> Decision:
        probes = _require_probes(inp, "proposed")
        if inp.history is None:
            raise ValueError("proposed needs a HistoryStore")
        n = network_scores(probes, self.cfg["network_score"])
        c = cost_scores(inp.providers, self.cfg["cost"], self.size_bytes)
        rho = {p: r.rho for p, r in risk_scores(inp.providers, inp.history, self.cfg["risk"]).items()}
        w, profile = self.rule(probes, inp.baseline, self.cfg)
        scores = {p: w["network"] * n[p] + w["cost"] * c[p] - w["risk"] * rho[p] for p in inp.providers}
        return Decision(self.name, rank(scores, inp.providers), scores=scores, weights=w, risk=rho,
                        profile=profile)


class StaticNetworkStrategy:
    name = Strategy.STATIC_NETWORK
    needs_probes = True

    def __init__(self, cfg: dict):
        self.ns_cfg = cfg["network_score"]
        self.weights = dict(cfg["static_network"]["weights"])

    def select(self, inp: SelectionInput) -> Decision:
        scores = network_scores(_require_probes(inp, "static_network"), self.ns_cfg)
        return Decision(self.name, rank(scores, inp.providers), scores=scores, weights=self.weights)


class CostOnlyStrategy:
    name = Strategy.COST_ONLY
    needs_probes = False

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def ranking(self, providers: list[str]) -> tuple[str, ...]:
        size = self.cfg["experiment"]["payload_size_bytes"]
        neg_cost = {p: -cost_per_transfer_usd(p, self.cfg["cost"], size) for p in providers}
        return rank(neg_cost, providers)

    def select(self, inp: SelectionInput) -> Decision:
        return Decision(self.name, self.ranking(inp.providers))


class NearestStrategy:
    name = Strategy.NEAREST
    needs_probes = False

    def __init__(self, nearest_order: list[str]):
        """nearest_order: all providers, closest first, decided once at setup."""
        self.order = tuple(nearest_order)

    def select(self, inp: SelectionInput) -> Decision:
        if set(self.order) != set(inp.providers):
            raise ValueError("nearest order does not match the providers")
        return Decision(self.name, self.order)


class RandomStrategy:
    name = Strategy.RANDOM
    needs_probes = False

    def __init__(self, seed: int):
        self.seed = seed

    def select(self, inp: SelectionInput) -> Decision:
        # Seeded per round, so a resumed block makes the same choices as an uninterrupted one.
        rng = random.Random(f"{self.seed}:{inp.round_key}")
        order = list(inp.providers)
        rng.shuffle(order)
        return Decision(self.name, tuple(order))


def nearest_order(cfg: dict, median_rtt_ms: dict[str, float] | None = None) -> list[str]:
    """Closest-first provider order for the nearest strategy, determined ONCE at setup.
    method=fixed:          fixed_provider first, then the rest in config order
    method=min_median_rtt: sorted by the median RTT measured at setup (passed in)."""
    providers = list(cfg["providers"])
    ncfg = cfg["nearest_region"]
    if ncfg["method"] == "fixed":
        first = ncfg["fixed_provider"]
        if first not in providers:
            raise ValueError(f"nearest_region.fixed_provider {first!r} is not a provider")
        return [first] + [p for p in providers if p != first]
    if ncfg["method"] == "min_median_rtt":
        if median_rtt_ms is None or set(median_rtt_ms) != set(providers):
            raise ValueError("min_median_rtt needs a measured median RTT for every provider")
        return list(rank({p: -median_rtt_ms[p] for p in providers}, providers))
    raise ValueError(f"unknown nearest_region.method {ncfg['method']!r}")


def check_cost_and_nearest_differ(cfg: dict, nearest: list[str]) -> None:
    """The design requires cost-only and fixed-nearest-region to pick different providers."""
    cheapest = CostOnlyStrategy(cfg).ranking(list(cfg["providers"]))[0]
    if cheapest == nearest[0]:
        raise ValueError(f"cost_only and nearest both choose {cheapest!r}; change regions or providers")


def build_strategies(cfg: dict, nearest: list[str]) -> dict[Strategy, object]:
    """All five strategies, keyed by Strategy. Checks the cost/nearest collapse first."""
    check_cost_and_nearest_differ(cfg, nearest)
    return {
        Strategy.PROPOSED: ProposedStrategy(cfg),
        Strategy.STATIC_NETWORK: StaticNetworkStrategy(cfg),
        Strategy.COST_ONLY: CostOnlyStrategy(cfg),
        Strategy.NEAREST: NearestStrategy(nearest),
        Strategy.RANDOM: RandomStrategy(cfg["random_strategy"]["seed"]),
    }
