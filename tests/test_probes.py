import socket

import pytest

from rmc.probing.probes import (MockProber, NetworkProber, calibrate, probe_all, save_baseline, summarise,
                                tcp_connect_ms)
from rmc.providers.mock import MockProvider


@pytest.fixture
def local_server():
    """A listening TCP socket on localhost. The kernel completes handshakes without accept()."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(64)
    yield s.getsockname()[1]
    s.close()


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_summarise():
    assert summarise([10.0, None, 30.0, 20.0]) == (20.0, 25.0)
    assert summarise([None, None]) == (None, 100.0)


def test_tcp_connect_to_listening_port(local_server):
    ms = tcp_connect_ms("127.0.0.1", local_server, timeout_s=1.0)
    assert ms is not None and 0 <= ms < 1000


def test_tcp_connect_to_closed_port_counts_as_lost():
    assert tcp_connect_ms("127.0.0.1", closed_port(), timeout_s=0.3) is None


def make_prober(cfg, payload, port, samples=3):
    cfg["probe"].update(port=port, samples=samples, sample_timeout_s=0.3)
    mocks = MockProvider.from_config(cfg)
    hosts = {n: "127.0.0.1" for n in cfg["providers"]}
    return NetworkProber(cfg, mocks, probe_file=payload, hosts=hosts), mocks


def test_network_prober_measures_rtt_loss_and_throughput(cfg, payload, local_server):
    prober, _ = make_prober(cfg, payload, local_server)
    r = prober.probe("aws_s3")
    assert r.ok and r.error is None
    assert r.rtt_ms is not None and r.loss_pct == 0.0
    assert r.throughput_mbps > 0 and r.probe_time_s > 0


def test_network_prober_reports_unreachable_endpoint(cfg, payload):
    prober, _ = make_prober(cfg, payload, closed_port(), samples=2)
    r = prober.probe("gcs")
    assert not r.ok and r.rtt_ms is None and r.loss_pct == 100.0
    assert "RTT" in r.error


def test_network_prober_reports_failed_throughput_probe(cfg, payload, local_server):
    prober, mocks = make_prober(cfg, payload, local_server)
    mocks["b2"].script("fail")
    r = prober.probe("b2")
    assert not r.ok and r.throughput_mbps is None and "throughput" in r.error


def test_probe_all_and_calibrate_with_mocks(cfg, tmp_path):
    mocks = MockProvider.from_config(cfg)
    prober = MockProber(mocks)
    names = list(cfg["providers"])
    assert set(probe_all(prober, names)) == set(names)

    baseline = calibrate(prober, names, samples=5)
    assert set(baseline) == set(names)
    assert baseline["aws_s3"]["rtt_ms"] < baseline["b2"]["rtt_ms"]    # 40 vs 250 ms in the mock config
    assert baseline["gcs"]["n"] == 5

    path = tmp_path / "cal.json"
    save_baseline(path, baseline)
    with pytest.raises(FileExistsError):
        save_baseline(path, baseline)


def test_repeated_probes_reuse_their_key_without_failing(cfg, payload, local_server, monkeypatch):
    """Regression: the probe reuses one key; providers that refuse overwrites must not break it."""
    monkeypatch.setenv("RMC_MACHINE_ID", "laptop-a")
    prober, _ = make_prober(cfg, payload, local_server)
    assert prober.probe_key == "rmc-probe/laptop-a.bin"
    assert all(prober.probe("gcs").ok for _ in range(3))
