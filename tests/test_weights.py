import pytest

from rmc.models import ProbeResult
from rmc.selection.weights import WEIGHT_RULES, get_rule, is_degraded, register, two_profile

BASELINE = {"a": {"rtt_ms": 100.0, "loss_pct": 0.0}, "b": {"rtt_ms": 200.0, "loss_pct": 0.0}}
IND = {"rtt_increase_ratio": 0.5, "loss_pct": 2.0}


def probes(rtt_a=100.0, loss_a=0.0, ok_a=True):
    return {"a": ProbeResult("a", rtt_a, 50.0, loss_a, 0.1, ok=ok_a),
            "b": ProbeResult("b", 200.0, 50.0, 0.0, 0.1)}


def test_natural_network_is_not_degraded():
    assert not is_degraded(probes(), BASELINE, IND)


def test_high_loss_is_degraded():
    assert is_degraded(probes(loss_a=2.0), BASELINE, IND)
    assert not is_degraded(probes(loss_a=1.9), BASELINE, IND)


def test_rtt_increase_vs_baseline_is_degraded():
    assert is_degraded(probes(rtt_a=150.0), BASELINE, IND)
    assert not is_degraded(probes(rtt_a=149.0), BASELINE, IND)


def test_without_baseline_only_loss_is_checked():
    assert not is_degraded(probes(rtt_a=1000.0), None, IND)
    assert is_degraded(probes(loss_a=5.0), None, IND)


def test_failed_probe_counts_as_degraded():
    assert is_degraded(probes(ok_a=False), BASELINE, IND)


def test_two_profile_returns_config_weights(cfg):
    w, profile = two_profile(probes(), BASELINE, cfg)
    assert profile == "normal" and w == cfg["weights"]["profiles"]["normal"]
    w, profile = two_profile(probes(loss_a=5.0), BASELINE, cfg)
    assert profile == "severe" and w == cfg["weights"]["profiles"]["severe"]


def test_shipped_profiles_match_intended_behaviour(cfg):
    """Design intent: under severe degradation cost matters less, risk matters more."""
    normal, severe = cfg["weights"]["profiles"]["normal"], cfg["weights"]["profiles"]["severe"]
    assert severe["cost"] < normal["cost"]
    assert severe["risk"] > normal["risk"]


def test_unknown_rule_raises():
    with pytest.raises(ValueError):
        get_rule("does_not_exist")


def test_custom_rule_can_be_registered():
    @register("test_constant")
    def constant(probes, baseline, cfg):
        return {"network": 1.0, "cost": 0.0, "risk": 0.0}, "const"
    try:
        assert get_rule("test_constant")(probes(), None, {})[1] == "const"
    finally:
        WEIGHT_RULES.pop("test_constant")

