import sqlite3

import pytest

from rmc.db import Database
from rmc.models import AttemptResult, Decision, ProbeResult, Strategy, UploadResult


def fake_transfer(provider="gcs", fail_first=False):
    attempts = []
    if fail_first:
        attempts.append(AttemptResult("aws_s3", "k", 60.0, success=False, timed_out=True, verified=None,
                                      error="timeout"))
    attempts.append(AttemptResult(provider, "k", 2.0, success=True, timed_out=False, verified=True))
    result = UploadResult(provider=provider, first_choice=attempts[0].provider, probe_time_s=0.5,
                          transfer_time_s=2.0, end_to_end_time_s=62.6 if fail_first else 2.5, success=True,
                          timed_out=fail_first, retry_time_s=60.1 if fail_first else None, verified=True,
                          attempts=tuple(attempts))
    decision = Decision(Strategy.PROPOSED, ranking=(attempts[0].provider, provider, "b2"),
                        scores={"aws_s3": 0.7, "gcs": 0.6, "b2": 0.1},
                        weights={"network": 0.4, "cost": 0.4, "risk": 0.2}, profile="normal")
    probes = [ProbeResult(p, 50.0, 20.0, 0.0, 0.2) for p in ("aws_s3", "gcs", "b2")]
    return decision, result, probes


def test_round_lifecycle_and_resume(tmp_path, ctx):
    db = Database(tmp_path / "t.sqlite")
    rr = db.start_round_run(ctx, round_id=1, is_warmup=False, condition="mild",
                            degraded_provider="gcs", strategy_order=["proposed"])
    d, r, probes = fake_transfer(fail_first=True)
    tid = db.insert_transfer(ctx, rr, 0, d, r, probes)
    assert db.completed_rounds("B01") == set()        # started, not complete
    db.complete_round_run(rr)
    assert db.completed_rounds("B01") == {1}

    row = db.conn.execute("SELECT * FROM transfers WHERE id = ?", (tid,)).fetchone()
    assert row["machine_id"] == "test-machine" and row["code_version"] == "abc123"
    assert row["condition"] == "mild" and row["degraded_provider"] == "gcs"
    assert row["n_failed_attempts"] == 1 and row["timestamp_utc"]
    assert db.conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 2
    assert db.conn.execute("SELECT COUNT(*) FROM probes").fetchone()[0] == 3


def test_rerun_of_crashed_round_gets_new_run_no(tmp_path, ctx):
    db = Database(tmp_path / "t.sqlite")
    first = db.start_round_run(ctx, 1, False, "natural", None, ["random"])   # crashed: never completed
    second = db.start_round_run(ctx, 1, False, "natural", None, ["random"])
    runs = db.conn.execute("SELECT id, run_no, status FROM round_runs ORDER BY id").fetchall()
    assert [(r["run_no"], r["status"]) for r in runs] == [(1, "started"), (2, "started")]
    assert first != second


def test_duplicate_strategy_in_round_is_rejected(tmp_path, ctx):
    db = Database(tmp_path / "t.sqlite")
    rr = db.start_round_run(ctx, 1, False, "natural", None, ["proposed"])
    d, r, _ = fake_transfer()
    db.insert_transfer(ctx, rr, 0, d, r)
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_transfer(ctx, rr, 0, d, r)


def test_complete_twice_is_rejected(tmp_path, ctx):
    db = Database(tmp_path / "t.sqlite")
    rr = db.start_round_run(ctx, 1, False, "natural", None, [])
    db.complete_round_run(rr)
    with pytest.raises(ValueError):
        db.complete_round_run(rr)


def test_meta_is_write_once(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    db.set_meta("schedule_seed", "42")
    db.set_meta("schedule_seed", "42")                # same value is fine
    with pytest.raises(ValueError):
        db.set_meta("schedule_seed", "43")


def test_recent_attempts_filters(tmp_path, ctx):
    db = Database(tmp_path / "t.sqlite")
    rr = db.start_round_run(ctx, 1, False, "severe", "aws_s3", ["proposed"])
    d, r, _ = fake_transfer(fail_first=True)
    db.insert_transfer(ctx, rr, 0, d, r)
    rows = db.recent_attempts("aws_s3", limit=10, block_id="B01", strategy="proposed")
    assert len(rows) == 1 and rows[0]["timed_out"] == 1
    assert db.recent_attempts("aws_s3", limit=10, strategy="random") == []


def test_backup_never_overwrites(tmp_path, ctx):
    db = Database(tmp_path / "t.sqlite")
    db.start_round_run(ctx, 1, False, "natural", None, [])
    b1 = db.backup(tmp_path / "bk", "B01")
    b2 = db.backup(tmp_path / "bk", "B01")
    assert b1 != b2 and b1.exists() and b2.exists()
    with sqlite3.connect(b1) as c:
        assert c.execute("SELECT COUNT(*) FROM round_runs").fetchone()[0] == 1
