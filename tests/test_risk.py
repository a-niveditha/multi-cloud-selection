import pytest

from rmc.models import AttemptResult, ProbeResult
from rmc.selection.risk import HistoryStore, risk_score


def ok(t=2.0, p="a"):
    return AttemptResult(p, "k", t, success=True, timed_out=False, verified=True)


def timeout(p="a"):
    return AttemptResult(p, "k", 60.0, success=False, timed_out=True, verified=None)


def fail(p="a"):
    return AttemptResult(p, "k", 1.0, success=False, timed_out=False, verified=None)


def bad_checksum(p="a"):
    return AttemptResult(p, "k", 2.0, success=True, timed_out=False, verified=False)


def probe(rtt=100.0, loss=0.0, p="a"):
    return ProbeResult(p, rtt, 50.0, loss, 0.1)


def test_cold_start_uses_configured_value(cfg):
    assert risk_score([], [], cfg["risk"]).rho == cfg["risk"]["cold_start_rho"]
    cfg["risk"]["cold_start_rho"] = 0.3
    assert risk_score([], [], cfg["risk"]).rho == 0.3


def test_reliable_stable_provider_has_zero_risk(cfg):
    r = risk_score([ok()] * 5, [probe()] * 5, cfg["risk"])
    assert r.rho == 0.0


def test_timeout_rate(cfg):
    r = risk_score([ok(), ok(), timeout(), timeout()], [], cfg["risk"])
    assert r.components["timeout_rate"] == 0.5
    assert r.components["failure_rate"] == 0.0
    assert r.rho == pytest.approx(cfg["risk"]["components"]["timeout_rate"] * 0.5)


def test_failure_rate_counts_errors_and_checksum_mismatch_not_timeouts(cfg):
    r = risk_score([ok(), fail(), bad_checksum(), timeout()], [], cfg["risk"])
    assert r.components["failure_rate"] == 0.5
    assert r.components["timeout_rate"] == 0.25


def test_variability_raises_risk(cfg):
    stable = risk_score([ok(2.0)] * 4, [probe(100, 0.0)] * 4, cfg["risk"]).rho
    unstable = risk_score([ok(1.0), ok(5.0), ok(1.0), ok(5.0)],
                          [probe(50, 0.0), probe(300, 4.0), probe(50, 0.0), probe(300, 4.0)], cfg["risk"])
    assert unstable.rho > stable
    assert unstable.components["transfer_time_cv"] > 0
    assert unstable.components["loss_std"] > 0
    assert unstable.components["rtt_cv"] > 0


def test_rho_stays_in_unit_interval(cfg):
    r = risk_score([timeout(), fail(), ok(0.1), ok(100.0)],
                   [probe(10, 0.0), probe(1000, 50.0)], cfg["risk"])
    assert 0.0 <= r.rho <= 1.0
    assert all(0.0 <= v <= 1.0 for v in r.components.values())


def test_failure_increases_recent_risk(cfg):
    h = HistoryStore(window_k=10)
    for _ in range(4):
        h.add_attempt(ok())
    before = risk_score(h.attempts("a"), h.probes("a"), cfg["risk"]).rho
    h.add_attempt(timeout())
    after = risk_score(h.attempts("a"), h.probes("a"), cfg["risk"]).rho
    assert after > before


def test_window_forgets_old_failures(cfg):
    h = HistoryStore(window_k=3)
    for _ in range(3):
        h.add_attempt(timeout())
    for _ in range(3):
        h.add_attempt(ok())
    assert len(h.attempts("a")) == 3
    assert risk_score(h.attempts("a"), [], cfg["risk"]).rho == 0.0


def test_history_is_per_provider_and_resettable():
    h = HistoryStore(window_k=5)
    h.add_attempt(timeout("a"))
    h.add_probe(probe(p="b"))
    assert len(h.attempts("a")) == 1 and h.attempts("b") == []
    assert len(h.probes("b")) == 1
    h.reset()
    assert h.attempts("a") == [] and h.probes("b") == []
