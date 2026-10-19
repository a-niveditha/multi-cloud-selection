import subprocess

import pytest

from rmc.netem.netem import MockNetem, TcNetem, uncovered_endpoint_ips
from rmc.providers.mock import MockProvider

TARGETS = {"gcs": ["142.250.0.0/15", "172.217.0.0/16"], "b2": []}


def test_dry_run_prints_and_runs_nothing():
    calls, printed = [], []
    n = TcNetem("eth0", TARGETS, {"gcs": 40.0}, dry_run=True, run=lambda *a, **k: calls.append(a), echo=printed.append)
    n.apply("gcs", rtt_increase_ratio=1.0, loss_pct=5.0)
    n.clear()
    assert calls == []
    assert printed[0].startswith("[dry-run] sudo -n tc qdisc add dev eth0 root handle 1: prio bands 4")
    assert not n.has_rules()


def test_apply_commands():
    n = TcNetem("enp0s3", TARGETS, {"gcs": 40.0}, dry_run=True, sudo=False, echo=lambda s: None)
    cmds = [" ".join(c) for c in n.apply_commands("gcs", 0.25, 1.0)]
    assert cmds == [
        "tc qdisc add dev enp0s3 root handle 1: prio bands 4",
        "tc qdisc add dev enp0s3 parent 1:4 handle 40: netem delay 10.0ms loss 1.0% limit 10000",
        "tc filter add dev enp0s3 parent 1: protocol ip prio 1 u32 match ip dst 142.250.0.0/15 flowid 1:4",
        "tc filter add dev enp0s3 parent 1: protocol ip prio 1 u32 match ip dst 172.217.0.0/16 flowid 1:4",
    ]


def test_refuses_empty_targets():
    n = TcNetem("eth0", TARGETS, {"b2": 250.0}, dry_run=True, echo=lambda s: None)
    with pytest.raises(ValueError):
        n.apply("b2", 1.0, 5.0)


class FakeRun:
    def __init__(self, qdisc_output="", del_stderr="", del_rc=0):
        self.calls, self.qdisc_output, self.del_stderr, self.del_rc = [], qdisc_output, del_stderr, del_rc

    def __call__(self, cmd, capture_output, text, check):
        self.calls.append(cmd)
        if "show" in cmd:
            return subprocess.CompletedProcess(cmd, 0, self.qdisc_output, "")
        if "del" in cmd:
            return subprocess.CompletedProcess(cmd, self.del_rc, "", self.del_stderr)
        return subprocess.CompletedProcess(cmd, 0, "", "")


def test_has_rules_parses_tc_output():
    with_netem = "qdisc prio 1: root refcnt 2 bands 4\nqdisc netem 40: parent 1:4 limit 10000 delay 10ms loss 1%\n"
    assert TcNetem("eth0", TARGETS, {}, dry_run=False, run=FakeRun(with_netem)).has_rules()
    assert not TcNetem("eth0", TARGETS, {}, dry_run=False, run=FakeRun("qdisc fq_codel 0: root refcnt 2\n")).has_rules()


def test_clear_tolerates_nothing_to_delete_but_not_other_errors():
    ok = FakeRun(del_rc=2, del_stderr="Error: Cannot delete qdisc with handle of zero.")
    TcNetem("eth0", TARGETS, {}, dry_run=False, run=ok).clear()
    assert ok.calls == [["sudo", "-n", "tc", "qdisc", "del", "dev", "eth0", "root"]]
    bad = FakeRun(del_rc=1, del_stderr="sudo: a password is required")
    with pytest.raises(RuntimeError):
        TcNetem("eth0", TARGETS, {}, dry_run=False, run=bad).clear()


def test_mock_netem_degrades_and_clears(cfg):
    mocks = MockProvider.from_config(cfg)
    n = MockNetem(mocks)
    assert not n.has_rules()
    n.apply("gcs", 1.0, 5.0)
    assert n.has_rules() and mocks["gcs"].rtt_ms == 2 * mocks["gcs"].base.rtt_ms
    n.clear()
    assert not n.has_rules()


def test_uncovered_endpoint_ips():
    assert uncovered_endpoint_ips("127.0.0.1", ["127.0.0.0/8"]) == []
    assert uncovered_endpoint_ips("127.0.0.1", ["10.0.0.0/8"]) == ["127.0.0.1"]
