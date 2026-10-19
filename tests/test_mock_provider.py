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


def test_loss_raises_failure_probability(payload):
    def failures(**kw):
        p = make(loss_fail_per_pct=0.05, **kw)
        return sum(not p.upload(payload, f"k{i}", 60).success for i in range(400))
    assert failures(loss_pct=0.0) == 0
    assert failures(loss_pct=5.0) > 100          # 5 % loss -> +25 % timeout and +25 % failure


def test_congestion_episode_slows_transfers(payload):
    p = make(episode_rate_per_hour=3600, episode_mean_s=1e9, episode_slowdown=4.0)
    normal = p.expected_transfer_time_s(payload.size_bytes)
    p.clock.sleep(100)                           # ~100 expected entries, practically certain
    p.probe()
    assert p.in_episode
    assert p.throughput_mbps == 10 / 4.0
    assert p.expected_transfer_time_s(payload.size_bytes) > normal


def test_episodes_off_by_default_keep_rng_stream(payload):
    p = make()
    p.clock.sleep(10_000)
    p.probe()
    assert not p.in_episode and p.throughput_mbps == 10


def test_simulation_section_builds_all_providers(cfg):
    sims = MockProvider.from_config(cfg, section="simulation", seed=1)
    assert list(sims) == list(cfg["providers"])
    assert all(s.base.episode_rate_per_hour > 0 for s in sims.values())
