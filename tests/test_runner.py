"""Runner tests: full mock blocks, crash + resume, and every preflight safety check."""
import pytest

from rmc.clock import SimClock
from rmc.config import config_hash
from rmc.db import Database
from rmc.models import RunContext
from rmc.netem.netem import MockNetem
from rmc.probing.probes import MockProber, calibrate
from rmc.providers.mock import MockProvider
from rmc.runner.runner import BlockRunner, RunnerRefused, preflight
from rmc.runner.schedule import generate_schedule
from rmc.runner.setup_file import build_setup


class CrashingProber:
    """Behaves like MockProber, then raises on the n-th probe call (simulated crash)."""

    def __init__(self, inner, crash_at):
        self.inner, self.crash_at, self.calls = inner, crash_at, 0

    def probe(self, name):
        self.calls += 1
        if self.calls == self.crash_at:
            raise RuntimeError("simulated crash")
        return self.inner.probe(name)


class Env:
    def __init__(self, cfg, tmp_path, payload):
        cfg["storage"]["backup_dir"] = str(tmp_path / "backups")
        self.cfg, self.payload, self.tmp_path = cfg, payload, tmp_path
        self.clock = SimClock()
        self.mocks = MockProvider.from_config(cfg, self.clock)
        self.netem = MockNetem(self.mocks)
        self.setup = build_setup(cfg, calibrate(MockProber(self.mocks), list(self.mocks), 5), "test-machine")
        self.schedule = generate_schedule(cfg, 42)
        self.db = Database(tmp_path / "trials.sqlite")

    def block_id(self, condition):
        return next(b["block_id"] for b in self.schedule["blocks"] if b["condition"] == condition)

    def ctx(self, block_id, machine="test-machine", **kw):
        return RunContext(machine, "abc123", block_id, config_hash(self.cfg), **kw)

    def runner(self, block_id, prober=None):
        return BlockRunner(self.cfg, self.schedule, block_id, self.db, self.ctx(block_id), self.mocks,
                           prober or MockProber(self.mocks), self.netem, self.setup, self.payload, self.clock,
                           log=lambda s: None)

    def count(self, sql, *args):
        return self.db.conn.execute(sql, args).fetchone()[0]


@pytest.fixture
def env(cfg, tmp_path, payload):
    e = Env(cfg, tmp_path, payload)
    yield e
    e.db.close()


def rounds_in_block(env, block_id):
    return len(next(b for b in env.schedule["blocks"] if b["block_id"] == block_id)["rounds"])


def test_full_degraded_block(env):
    bid = env.block_id("severe")
    block = next(b for b in env.schedule["blocks"] if b["block_id"] == bid)
    n_rounds = rounds_in_block(env, bid)
    summary = env.runner(bid).run()

    assert summary == {"block_id": bid, "rounds_run": n_rounds, "rounds_skipped": 0, "transfers": n_rounds * 5}
    assert env.count("SELECT COUNT(*) FROM transfers WHERE block_id = ?", bid) == n_rounds * 5
    assert env.db.completed_rounds(bid) == set(range(1, n_rounds + 1))
    assert env.count("SELECT COUNT(*) FROM transfers WHERE condition = 'severe' AND degraded_provider = ?",
                     block["degraded_provider"]) == n_rounds * 5
    # netem: cleared, applied to the scheduled provider, cleared again at the end
    assert env.netem.log[0] == ("clear",)
    assert env.netem.log[1][:2] == ("apply", block["degraded_provider"])
    assert env.netem.log[-1] == ("clear",) and not env.netem.has_rules()
    assert len(list((env.tmp_path / "backups").glob(f"trials_{bid}_*.sqlite"))) == 1


def test_probes_are_recorded_only_for_network_aware_strategies(env):
    env.cfg["probe"]["max_age_s"] = 0       # probe before every decision
    bid = env.block_id("natural")
    env.runner(bid).run()
    per_strategy = dict(env.db.conn.execute(
        "SELECT t.strategy, COUNT(p.id) FROM transfers t LEFT JOIN probes p ON p.transfer_id = t.id "
        "GROUP BY t.strategy").fetchall())
    n = rounds_in_block(env, bid)
    assert per_strategy["proposed"] == per_strategy["static_network"] == 3 * n
    assert per_strategy["cost_only"] == per_strategy["nearest"] == per_strategy["random"] == 0
    assert env.count("SELECT MIN(probe_time_s) FROM transfers WHERE strategy = 'proposed'") > 0
    assert env.count("SELECT MAX(probe_time_s) FROM transfers WHERE strategy = 'cost_only'") == 0


def test_crash_then_resume(env):
    env.cfg["probe"]["max_age_s"] = 0       # probe before every decision
    bid = env.block_id("mild")
    n_rounds = rounds_in_block(env, bid)
    # 6 probe calls per round (2 probing strategies x 3 providers): call 16 is mid-way through round 3
    with pytest.raises(RuntimeError, match="simulated crash"):
        env.runner(bid, CrashingProber(MockProber(env.mocks), crash_at=16)).run()

    assert env.db.completed_rounds(bid) == {1, 2}
    assert not env.netem.has_rules()                                     # cleared in finally
    assert list((env.tmp_path / "backups").glob(f"trials_{bid}-incomplete_*.sqlite"))
    partial = env.count("SELECT COUNT(*) FROM transfers t JOIN round_runs r ON t.round_run_id = r.id "
                        "WHERE r.round_id = 3 AND r.status = 'started'")
    assert partial >= 1                                                  # partial round kept, not deleted

    # history rebuilt on resume ignores the crashed run's attempts
    all_attempts = env.count("SELECT COUNT(*) FROM attempts WHERE block_id = ?", bid)
    assert len(env.db.history_attempts(bid, None)) < all_attempts

    summary = env.runner(bid).run()
    assert summary["rounds_skipped"] == 2 and summary["rounds_run"] == n_rounds - 2
    assert env.db.completed_rounds(bid) == set(range(1, n_rounds + 1))
    runs = env.db.conn.execute("SELECT run_no, status FROM round_runs WHERE block_id = ? AND round_id = 3 "
                               "ORDER BY run_no", (bid,)).fetchall()
    assert [tuple(r) for r in runs] == [(1, "started"), (2, "complete")]
    complete = env.count("SELECT COUNT(*) FROM transfers t JOIN round_runs r ON t.round_run_id = r.id "
                         "WHERE r.status = 'complete' AND t.block_id = ?", bid)
    assert complete == n_rounds * 5


def test_rerunning_a_finished_block_does_nothing(env):
    bid = env.block_id("natural")
    env.runner(bid).run()
    before = env.count("SELECT COUNT(*) FROM transfers")
    summary = env.runner(bid).run()
    assert summary["rounds_run"] == 0 and env.count("SELECT COUNT(*) FROM transfers") == before


def test_natural_block_refused_if_netem_rules_present(env):
    env.mocks["gcs"].degrade(1.0, 5.0)                                   # someone left rules behind
    with pytest.raises(RunnerRefused, match="NATURAL"):
        env.runner(env.block_id("natural")).run()
    assert env.netem.log == []                                           # refused before touching anything
    assert env.count("SELECT COUNT(*) FROM round_runs") == 0


def test_random_strategy_is_the_same_after_resume(env):
    bid = env.block_id("natural")
    env.runner(bid).run()
    first = env.db.conn.execute("SELECT round_id, first_choice FROM transfers WHERE strategy = 'random' "
                                "ORDER BY round_id").fetchall()
    other = Env(env.cfg, env.tmp_path / "second", env.payload)
    other.runner(bid).run()
    second = other.db.conn.execute("SELECT round_id, first_choice FROM transfers WHERE strategy = 'random' "
                                   "ORDER BY round_id").fetchall()
    other.db.close()
    assert [tuple(r) for r in first] == [tuple(r) for r in second]


# ---- preflight ------------------------------------------------------------------
def check(env, bid, ctx=None, git_dirty=False, allow_dirty=False, netem_dry_run=False, schedule=None):
    preflight(env.cfg, schedule or env.schedule, bid, env.db, ctx or env.ctx(bid), env.setup,
              git_dirty=git_dirty, allow_dirty=allow_dirty, netem_dry_run=netem_dry_run)


def test_preflight_passes_on_clean_tree(env):
    check(env, env.block_id("mild"))


def test_preflight_refuses_dirty_tree_unless_overridden(env):
    bid = env.block_id("natural")
    with pytest.raises(RunnerRefused, match="uncommitted"):
        check(env, bid, git_dirty=True)
    with pytest.raises(RunnerRefused, match="not a git repository"):
        check(env, bid, git_dirty=None)
    check(env, bid, git_dirty=True, allow_dirty=True)


def test_preflight_refuses_changed_config(env):
    bid = env.block_id("natural")
    env.schedule["config_hash"] = "000000000000"
    with pytest.raises(RunnerRefused, match="config.yaml changed"):
        check(env, bid)


def test_preflight_refuses_block_started_on_another_machine(env):
    bid = env.block_id("natural")
    env.db.start_round_run(env.ctx(bid, machine="other-laptop"), 1, True, "natural", None, [])
    with pytest.raises(RunnerRefused, match="another machine"):
        check(env, bid)


def test_preflight_refuses_degraded_block_with_dry_run_netem_except_pilot(env):
    bid = env.block_id("severe")
    with pytest.raises(RunnerRefused, match="dry_run"):
        check(env, bid, netem_dry_run=True)
    check(env, bid, ctx=env.ctx(bid, is_pilot=True), netem_dry_run=True)
    check(env, env.block_id("natural"), netem_dry_run=True)              # natural needs no netem


def test_preflight_refuses_database_of_another_schedule(env):
    bid = env.block_id("natural")
    check(env, bid)
    other = generate_schedule(env.cfg, 7)
    with pytest.raises(RunnerRefused, match="different schedule"):
        check(env, bid, schedule=other)


def test_preflight_refuses_cost_and_nearest_collapse(env):
    env.setup["nearest_order"] = ["b2", "aws_s3", "gcs"]                 # b2 is also the cheapest
    with pytest.raises(ValueError, match="cost_only and nearest"):
        env.runner(env.block_id("natural"))


def test_probe_reuse_skips_probing_while_fresh(env):
    env.cfg["probe"]["max_age_s"] = 1e9          # never stale within a block
    env.cfg["probe"]["reprobe_after_failure"] = False
    bid = env.block_id("natural")
    env.runner(bid).run()
    n = rounds_in_block(env, bid)
    # each network-aware strategy probes once (3 providers), then reuses
    for s in ("proposed", "static_network"):
        assert env.count("SELECT COUNT(*) FROM probes p JOIN transfers t ON p.transfer_id = t.id "
                         "WHERE t.strategy = ?", s) == 3
        assert env.count("SELECT COUNT(*) FROM transfers WHERE strategy = ? AND probe_time_s > 0", s) == 1
        assert env.count("SELECT COUNT(*) FROM transfers WHERE strategy = ?", s) == n


@pytest.mark.parametrize("reprobe", [True, False])
def test_probe_reuse_reprobes_after_failure(env, reprobe):
    env.cfg["probe"]["max_age_s"] = 1e9
    env.cfg["probe"]["reprobe_after_failure"] = reprobe
    for m in env.mocks.values():
        m.base.failure_prob = 1.0                 # every upload attempt fails
    bid = env.block_id("natural")
    env.runner(bid).run()
    probed = env.count("SELECT COUNT(*) FROM transfers WHERE strategy = 'proposed' AND probe_time_s > 0")
    assert probed == (rounds_in_block(env, bid) if reprobe else 1)


def test_mock_probe_costed_like_real_probe(cfg):
    mocks = MockProvider.from_config(cfg)
    heavy = MockProber(mocks, pcfg={"samples": 10, "throughput_bytes": 524288}).probe("b2")
    light = MockProber(mocks, pcfg={"samples": 3, "throughput_bytes": 131072}).probe("b2")
    assert heavy.probe_time_s > 10 * mocks["b2"].base.rtt_ms / 1000 * 0.5
    assert light.probe_time_s < heavy.probe_time_s
    assert light.loss_pct in {0.0, 100 / 3, 200 / 3, 100.0}   # coarse: steps of 1/samples
