from rmc.probing.probes import MockProber, calibrate, probe_all
from rmc.providers.mock import MockProvider


def test_probe_all_and_calibrate_with_mocks(cfg):
    mocks = MockProvider.from_config(cfg)
    prober = MockProber(mocks)
    names = list(cfg["providers"])
    assert set(probe_all(prober, names)) == set(names)

    baseline = calibrate(prober, names, samples=5)
    assert set(baseline) == set(names)
    assert baseline["aws_s3"]["rtt_ms"] < baseline["b2"]["rtt_ms"]    # 40 vs 250 ms in the mock config
    assert baseline["gcs"]["n"] == 5


def test_probe_reports_failed_throughput_when_all_samples_lost(cfg):
    mocks = MockProvider.from_config(cfg)
    mocks["b2"].degrade(0.0, 100.0)                                   # every sample lost
    r = MockProber(mocks, pcfg=cfg["probe"]).probe("b2")
    assert r.loss_pct == 100.0
