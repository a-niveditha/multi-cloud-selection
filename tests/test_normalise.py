import pytest

from rmc.models import ProbeResult
from rmc.selection.normalise import cost_per_transfer_usd, cost_scores, min_max, network_scores, reference


def probe(name, rtt, thr, loss, ok=True):
    return ProbeResult(name, rtt, thr, loss, probe_time_s=0.1, ok=ok)


def test_min_max_higher_and_lower_is_better():
    assert min_max({"a": 10, "b": 20, "c": 30}, higher_is_better=True) == {"a": 0.0, "b": 0.5, "c": 1.0}
    assert min_max({"a": 10, "b": 20, "c": 30}, higher_is_better=False) == {"a": 1.0, "b": 0.5, "c": 0.0}


def test_min_max_all_equal_gives_one():
    assert min_max({"a": 5, "b": 5}, higher_is_better=False) == {"a": 1.0, "b": 1.0}


def test_reference_is_clipped():
    assert reference(50, 0, 100, higher_is_better=True) == 0.5
    assert reference(150, 0, 100, higher_is_better=True) == 1.0
    assert reference(150, 0, 100, higher_is_better=False) == 0.0
    assert reference(-5, 0, 100, higher_is_better=False) == 1.0


NS = {"components": {"rtt": 0.5, "throughput": 0.5, "loss": 0.0}, "normalisation": "min_max",
      "reference": {"rtt_ms": [0, 200], "throughput_mbps": [0, 100], "loss_pct": [0, 10]}}


def test_network_scores_min_max():
    probes = {"a": probe("a", 50, 100, 0), "b": probe("b", 100, 50, 1), "c": probe("c", 150, 0, 2)}
    assert network_scores(probes, NS) == pytest.approx({"a": 1.0, "b": 0.5, "c": 0.0})


def test_failed_probe_scores_zero_and_is_excluded_from_range():
    probes = {"a": probe("a", 50, 100, 0), "b": probe("b", 100, 50, 0),
              "c": probe("c", None, None, None, ok=False)}
    n = network_scores(probes, NS)
    assert n["c"] == 0.0
    assert n["a"] == pytest.approx(1.0) and n["b"] == pytest.approx(0.0)


def test_network_scores_reference():
    ns = dict(NS, normalisation="reference")
    probes = {"a": probe("a", 50, 100, 0), "b": probe("b", 200, 50, 0)}
    # a: rtt 1 - 50/200 = 0.75, thr 1.0 -> 0.875 ; b: rtt 0, thr 0.5 -> 0.25
    assert network_scores(probes, ns) == pytest.approx({"a": 0.875, "b": 0.25})


def test_lower_loss_is_better():
    ns = dict(NS, components={"rtt": 0.0, "throughput": 0.0, "loss": 1.0})
    probes = {"a": probe("a", 50, 50, 0.0), "b": probe("b", 50, 50, 5.0)}
    n = network_scores(probes, ns)
    assert n["a"] > n["b"]


COST = {"storage_months": 1, "egress_fraction": 1.0, "normalisation": "min_max", "table": {
    "a": {"storage_usd_per_gb_month": 0.03, "egress_usd_per_gb": 0.0},
    "b": {"storage_usd_per_gb_month": 0.02, "egress_usd_per_gb": 0.0},
    "c": {"storage_usd_per_gb_month": 0.01, "egress_usd_per_gb": 0.0}}}


def test_cost_per_transfer():
    assert cost_per_transfer_usd("a", COST, 1_000_000_000) == pytest.approx(0.03)


def test_cost_scores_cheaper_is_higher():
    assert cost_scores(["a", "b", "c"], COST, 10_485_760) == pytest.approx({"a": 0.0, "b": 0.5, "c": 1.0})
    ratio = cost_scores(["a", "b", "c"], dict(COST, normalisation="ratio"), 10_485_760)
    assert ratio == pytest.approx({"a": 1 / 3, "b": 0.5, "c": 1.0})


def test_shipped_cost_table_makes_b2_cheapest(cfg):
    c = cost_scores(list(cfg["providers"]), cfg["cost"], cfg["experiment"]["payload_size_bytes"])
    assert max(c, key=c.get) == "b2"
