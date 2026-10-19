import pytest

from rmc.clock import SimClock
from rmc.models import Decision, Strategy
from rmc.providers.mock import MockNetwork, MockProvider
from rmc.transfer import transfer_with_failover

TCFG = {"timeout_s": 30, "max_attempts": 3, "retry_on_verify_failure": True}


@pytest.fixture
def setup():
    clock = SimClock()
    mocks = {n: MockProvider(n, MockNetwork(rtt_ms=100, throughput_mbps=10, loss_pct=0), seed=i, clock=clock)
             for i, n in enumerate(["aws_s3", "gcs", "b2"])}
    decision = Decision(Strategy.PROPOSED, ranking=("gcs", "aws_s3", "b2"))
    return clock, mocks, decision


def run(setup, payload, tcfg=TCFG, probe_time_s=1.5):
    clock, mocks, decision = setup
    return transfer_with_failover(decision, mocks, payload, "rmc/B01/R1/run1/proposed", tcfg,
                                  probe_time_s=probe_time_s, clock=clock)


def test_success_without_retry(setup, payload):
    _, mocks, _ = setup
    r = run(setup, payload)
    t = mocks["gcs"].expected_transfer_time_s(payload.size_bytes)
    assert r.success and r.verified and r.provider == r.first_choice == "gcs"
    assert len(r.attempts) == 1 and r.retry_time_s is None and not r.timed_out
    assert r.transfer_time_s == pytest.approx(t)
    assert r.end_to_end_time_s == pytest.approx(1.5 + t)


def test_timeout_fails_over_to_next_best(setup, payload):
    _, mocks, _ = setup
    mocks["gcs"].script("timeout")
    r = run(setup, payload)
    t2 = mocks["aws_s3"].expected_transfer_time_s(payload.size_bytes)
    assert r.success and r.timed_out
    assert r.first_choice == "gcs" and r.provider == "aws_s3"
    assert [a.provider for a in r.attempts] == ["gcs", "aws_s3"]
    assert r.attempts[0].transfer_time_s == 30                 # time-to-failure
    assert r.retry_time_s == pytest.approx(30)                 # failure-to-retry
    assert r.end_to_end_time_s == pytest.approx(1.5 + 30 + t2)
    assert r.n_failed_attempts == 1


def test_all_providers_fail(setup, payload):
    _, mocks, _ = setup
    for m in mocks.values():
        m.script("fail")
    r = run(setup, payload)
    assert not r.success and not r.verified
    assert len(r.attempts) == 3 and r.n_failed_attempts == 3


def test_max_attempts_limits_failover(setup, payload):
    _, mocks, _ = setup
    mocks["gcs"].script("timeout")
    r = run(setup, payload, dict(TCFG, max_attempts=1))
    assert len(r.attempts) == 1 and not r.success and r.retry_time_s is None


def test_checksum_mismatch_triggers_retry(setup, payload):
    _, mocks, _ = setup
    mocks["gcs"].script("corrupt")
    r = run(setup, payload)
    assert r.success and r.provider == "aws_s3"
    a0 = r.attempts[0]
    assert a0.success and a0.verified is False
    assert r.retry_time_s == pytest.approx(a0.transfer_time_s + a0.verify_time_s)


def test_checksum_mismatch_without_retry_is_reported_as_failure(setup, payload):
    _, mocks, _ = setup
    mocks["gcs"].script("corrupt")
    r = run(setup, payload, dict(TCFG, retry_on_verify_failure=False))
    assert len(r.attempts) == 1 and not r.success and not r.verified


def test_each_attempt_has_a_unique_key(setup, payload):
    _, mocks, _ = setup
    mocks["gcs"].script("fail")
    r = run(setup, payload)
    keys = [a.object_key for a in r.attempts]
    assert keys == ["rmc/B01/R1/run1/proposed/1-gcs.bin", "rmc/B01/R1/run1/proposed/2-aws_s3.bin"]
