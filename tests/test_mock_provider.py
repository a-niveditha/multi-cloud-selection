from rmc.clock import SimClock
from rmc.providers.mock import MockNetwork, MockProvider


def make(**kw):
    net = dict(rtt_ms=100, throughput_mbps=10, loss_pct=0.0)
    net.update(kw)
    return MockProvider("p", MockNetwork(**net), seed=1, clock=SimClock())


def test_successful_upload_is_verified_and_takes_simulated_time(payload):
    p = make()
    r = p.upload(payload, "k1", timeout_s=60)
    assert r.success and r.verified and r.ok and not r.timed_out
    assert abs(r.transfer_time_s - p.expected_transfer_time_s(payload.size_bytes)) < 1e-9


def test_scripted_timeout_reports_time_to_failure(payload):
    p = make()
    p.script("timeout")
    r = p.upload(payload, "k1", timeout_s=30)
    assert r.timed_out and not r.success and r.verified is None
    assert r.transfer_time_s == 30


def test_scripted_failure(payload):
    p = make()
    p.script("fail")
    r = p.upload(payload, "k1", timeout_s=30)
    assert not r.success and not r.timed_out and "simulated upload failure" in r.error


def test_corrupt_upload_fails_verification(payload):
    p = make()
    p.script("corrupt")
    r = p.upload(payload, "k1", timeout_s=30)
    assert r.success and r.verified is False and not r.ok


def test_slow_transfer_times_out(payload):
    p = make(throughput_mbps=0.001)
    r = p.upload(payload, "k1", timeout_s=5)
    assert r.timed_out and r.transfer_time_s == 5


def test_degradation_raises_rtt_loss_and_time(payload):
    p = make(loss_pct=0.0)
    before = p.expected_transfer_time_s(payload.size_bytes)
    p.degrade(rtt_increase_ratio=1.0, loss_pct=5.0)
    assert p.rtt_ms == 200 and p.loss_pct == 5.0
    assert p.expected_transfer_time_s(payload.size_bytes) > before
    p.clear_degradation()
    assert p.expected_transfer_time_s(payload.size_bytes) == before


def test_same_seed_same_results(payload, cfg):
    def run():
        mocks = MockProvider.from_config(cfg)
        return [mocks["b2"].upload(payload, f"k{i}", 60).transfer_time_s for i in range(5)]
    assert run() == run()


def test_probe_returns_values(cfg):
    m = MockProvider.from_config(cfg)["gcs"]
    r = m.probe()
    assert r.ok and r.rtt_ms > 0 and r.throughput_mbps > 0 and r.probe_time_s > 0
