from collections import Counter

import pytest

from rmc.models import AttemptResult, ProbeResult, Strategy
from rmc.selection.risk import HistoryStore
from rmc.selection.strategies import (CostOnlyStrategy, NearestStrategy, ProposedStrategy, RandomStrategy,
                                      SelectionInput, StaticNetworkStrategy, build_strategies,
                                      check_cost_and_nearest_differ, nearest_order, rank)
from rmc.selection.weights import WEIGHT_RULES, register

P = ["aws_s3", "gcs", "b2"]


def probes(rtt, loss=None):
    """rtt / loss: dicts provider -> value. Throughput is equal for everyone."""
    loss = loss or {}
    return {p: ProbeResult(p, rtt[p], 50.0, loss.get(p, 0.0), 0.1) for p in P}


@pytest.fixture
def scfg(cfg):
    """Config with round numbers so scores can be checked by hand."""
    cfg["network_score"]["components"] = {"rtt": 1.0, "throughput": 0.0, "loss": 0.0}
    cfg["network_score"]["normalisation"] = "min_max"
    cfg["cost"]["normalisation"] = "min_max"
    cfg["cost"]["table"] = {   # aws most expensive, b2 cheapest -> C = 0, 0.5, 1
        "aws_s3": {"storage_usd_per_gb_month": 0.03, "egress_usd_per_gb": 0.0},
        "gcs": {"storage_usd_per_gb_month": 0.02, "egress_usd_per_gb": 0.0},
        "b2": {"storage_usd_per_gb_month": 0.01, "egress_usd_per_gb": 0.0}}
    cfg["risk"]["components"] = {"timeout_rate": 0.5, "failure_rate": 0.5, "transfer_time_cv": 0.0,
                                 "loss_std": 0.0, "rtt_cv": 0.0}
    cfg["risk"]["cold_start_rho"] = 0.0
    cfg["weights"]["profiles"]["normal"] = {"network": 0.4, "cost": 0.4, "risk": 0.2}
    cfg["weights"]["profiles"]["severe"] = {"network": 0.45, "cost": 0.1, "risk": 0.45}
    return cfg


# RTT 50/75/150 -> N = 1, 0.75, 0
RTT = {"aws_s3": 50.0, "gcs": 75.0, "b2": 150.0}


def test_rank_breaks_ties_by_config_order():
    assert rank({"aws_s3": 0.4, "gcs": 0.5, "b2": 0.4}, P) == ("gcs", "aws_s3", "b2")


def test_proposed_score_formula(scfg):
    d = ProposedStrategy(scfg).select(SelectionInput(P, probes(RTT), HistoryStore(10)))
    # normal profile, no risk: aws .4*1 + .4*0 = .4 ; gcs .4*.75 + .4*.5 = .5 ; b2 0 + .4*1 = .4
    assert d.scores == pytest.approx({"aws_s3": 0.4, "gcs": 0.5, "b2": 0.4})
    assert d.ranking == ("gcs", "aws_s3", "b2")
    assert d.profile == "normal" and d.risk == {"aws_s3": 0.0, "gcs": 0.0, "b2": 0.0}


def test_proposed_risk_penalty_changes_choice(scfg):
    h = HistoryStore(10)
    for _ in range(3):
        h.add_attempt(AttemptResult("gcs", "k", 60.0, success=False, timed_out=True, verified=None))
    d = ProposedStrategy(scfg).select(SelectionInput(P, probes(RTT), h))
    # gcs rho = 0.5 * 1.0 = 0.5 -> 0.5 - 0.2*0.5 = 0.4: ties with aws, config order puts aws first
    assert d.risk["gcs"] == pytest.approx(0.5)
    assert d.scores["gcs"] == pytest.approx(0.4)
    assert d.chosen == "aws_s3"


def test_proposed_switches_to_severe_profile(scfg):
    d = ProposedStrategy(scfg).select(SelectionInput(P, probes(RTT, loss={"gcs": 5.0}), HistoryStore(10)))
    assert d.profile == "severe"
    assert d.weights == scfg["weights"]["profiles"]["severe"]


def test_proposed_uses_pluggable_rule(scfg):
    @register("test_network_only")
    def network_only(probes, baseline, cfg):
        return {"network": 1.0, "cost": 0.0, "risk": 0.0}, "x"
    try:
        scfg["weights"]["rule"] = "test_network_only"
        d = ProposedStrategy(scfg).select(SelectionInput(P, probes(RTT), HistoryStore(10)))
        assert d.chosen == "aws_s3" and d.profile == "x"
    finally:
        WEIGHT_RULES.pop("test_network_only")


def test_proposed_needs_probes_and_history(scfg):
    s = ProposedStrategy(scfg)
    with pytest.raises(ValueError):
        s.select(SelectionInput(P, None, HistoryStore(10)))
    with pytest.raises(ValueError):
        s.select(SelectionInput(P, probes(RTT), None))


def test_static_network_uses_only_live_network(scfg):
    h = HistoryStore(10)
    for _ in range(5):   # history must be ignored
        h.add_attempt(AttemptResult("aws_s3", "k", 60.0, success=False, timed_out=True, verified=None))
    d = StaticNetworkStrategy(scfg).select(SelectionInput(P, probes(RTT), h))
    assert d.ranking == ("aws_s3", "gcs", "b2")
    assert d.scores == pytest.approx({"aws_s3": 1.0, "gcs": 0.75, "b2": 0.0})
    assert d.risk == {}


def test_static_network_follows_degradation(scfg):
    d = StaticNetworkStrategy(scfg).select(SelectionInput(P, probes({"aws_s3": 300.0, "gcs": 75.0, "b2": 150.0})))
    assert d.chosen == "gcs"


def test_cost_only_picks_cheapest_regardless_of_network(scfg):
    d = CostOnlyStrategy(scfg).select(SelectionInput(P, probes({"aws_s3": 10.0, "gcs": 10.0, "b2": 999.0})))
    assert d.ranking == ("b2", "gcs", "aws_s3")


def test_nearest_is_fixed(scfg):
    s = NearestStrategy(["aws_s3", "gcs", "b2"])
    assert s.select(SelectionInput(P)).ranking == ("aws_s3", "gcs", "b2")
    assert s.select(SelectionInput(P, probes({"aws_s3": 999.0, "gcs": 1.0, "b2": 1.0}))).chosen == "aws_s3"


def test_nearest_order_methods(cfg):
    cfg["nearest_region"]["method"] = "min_median_rtt"
    assert nearest_order(cfg, {"aws_s3": 80.0, "gcs": 40.0, "b2": 200.0}) == ["gcs", "aws_s3", "b2"]
    with pytest.raises(ValueError):
        nearest_order(cfg, None)
    cfg["nearest_region"].update(method="fixed", fixed_provider="gcs")
    assert nearest_order(cfg) == ["gcs", "aws_s3", "b2"]


def test_random_is_reproducible_per_round_and_roughly_uniform():
    s = RandomStrategy(seed=123)
    assert s.select(SelectionInput(P, round_key="B01:R1")).ranking == \
        RandomStrategy(seed=123).select(SelectionInput(P, round_key="B01:R1")).ranking
    firsts = Counter(s.select(SelectionInput(P, round_key=f"B01:R{i}")).chosen for i in range(600))
    assert set(firsts) == set(P)
    assert all(150 <= n <= 250 for n in firsts.values())   # expected 200 each


def test_every_strategy_ranks_all_providers(cfg):
    strategies = build_strategies(cfg, ["aws_s3", "gcs", "b2"])
    assert set(strategies) == set(Strategy)
    inp = SelectionInput(P, probes(RTT), HistoryStore(10), round_key="B01:R1")
    for strategy, s in strategies.items():
        d = s.select(inp)
        assert d.strategy == strategy
        assert sorted(d.ranking) == sorted(P)


def test_cost_only_and_nearest_must_differ(cfg):
    check_cost_and_nearest_differ(cfg, ["aws_s3", "gcs", "b2"])   # b2 is cheapest: OK
    with pytest.raises(ValueError):
        check_cost_and_nearest_differ(cfg, ["b2", "aws_s3", "gcs"])
    with pytest.raises(ValueError):
        build_strategies(cfg, ["b2", "aws_s3", "gcs"])
