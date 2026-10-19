"""SQLite storage for trial data.

Rules enforced here:
  * Rows are only ever INSERTed. The one UPDATE is marking a round run complete.
  * A paired round is recorded as a "round run". If a block crashes mid-round,
    that run stays 'started' (kept for audit) and the runner starts a new run of
    the same round. Analysis uses only 'complete' runs.
  * Every row carries machine_id, code_version, block_id and a UTC timestamp.
  * Backups never overwrite an existing file.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from rmc.models import AttemptResult, Decision, ProbeResult, RunContext, UploadResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per execution of a paired round (all strategies, same machine).
CREATE TABLE IF NOT EXISTS round_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id          TEXT    NOT NULL,
    round_id          INTEGER NOT NULL,
    run_no            INTEGER NOT NULL,           -- 1, or 2+ if a crashed run was repeated
    is_warmup         INTEGER NOT NULL,
    condition         TEXT    NOT NULL,
    degraded_provider TEXT,                       -- NULL for natural
    strategy_order    TEXT    NOT NULL,           -- JSON list, from the schedule
    status            TEXT    NOT NULL CHECK (status IN ('started', 'complete')),
    started_at        TEXT    NOT NULL,
    completed_at      TEXT,
    machine_id        TEXT    NOT NULL,
    code_version      TEXT    NOT NULL,
    config_hash       TEXT    NOT NULL,
    is_pilot          INTEGER NOT NULL,
    UNIQUE (block_id, round_id, run_no)
);

-- One row per strategy per round run: the whole transfer after failover.
CREATE TABLE IF NOT EXISTS transfers (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    round_run_id       INTEGER NOT NULL REFERENCES round_runs(id),
    block_id           TEXT    NOT NULL,
    round_id           INTEGER NOT NULL,
    is_warmup          INTEGER NOT NULL,
    condition          TEXT    NOT NULL,
    degraded_provider  TEXT,
    strategy           TEXT    NOT NULL,
    strategy_position  INTEGER NOT NULL,          -- 0-based position in the randomised order
    first_choice       TEXT    NOT NULL,
    provider_final     TEXT    NOT NULL,
    probe_time_s       REAL    NOT NULL,
    transfer_time_s    REAL    NOT NULL,
    end_to_end_time_s  REAL    NOT NULL,
    success            INTEGER NOT NULL,
    timed_out          INTEGER NOT NULL,
    n_failed_attempts  INTEGER NOT NULL,
    retry_time_s       REAL,
    verified           INTEGER NOT NULL,
    ranking_json       TEXT    NOT NULL,
    scores_json        TEXT    NOT NULL,
    weights_json       TEXT    NOT NULL,
    risk_json          TEXT    NOT NULL,
    weight_profile     TEXT,
    machine_id         TEXT    NOT NULL,
    code_version       TEXT    NOT NULL,
    timestamp_utc      TEXT    NOT NULL,
    UNIQUE (round_run_id, strategy)
);

-- One row per upload attempt (first choice and each failover). Feeds risk history.
CREATE TABLE IF NOT EXISTS attempts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_id      INTEGER NOT NULL REFERENCES transfers(id),
    attempt_no       INTEGER NOT NULL,            -- 1 = first choice
    block_id         TEXT    NOT NULL,
    strategy         TEXT    NOT NULL,
    provider         TEXT    NOT NULL,
    object_key       TEXT    NOT NULL,
    transfer_time_s  REAL    NOT NULL,            -- time-to-failure for timeouts
    success          INTEGER NOT NULL,
    timed_out        INTEGER NOT NULL,
    verified         INTEGER,                     -- NULL = not checked
    verify_time_s    REAL    NOT NULL,
    error            TEXT,
    machine_id       TEXT    NOT NULL,
    code_version     TEXT    NOT NULL,
    timestamp_utc    TEXT    NOT NULL,
    UNIQUE (transfer_id, attempt_no)
);

-- One row per provider probed before a decision (network-aware strategies only).
CREATE TABLE IF NOT EXISTS probes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_id      INTEGER NOT NULL REFERENCES transfers(id),
    block_id         TEXT    NOT NULL,
    provider         TEXT    NOT NULL,
    rtt_ms           REAL,
    throughput_mbps  REAL,
    loss_pct         REAL,
    probe_time_s     REAL    NOT NULL,
    ok               INTEGER NOT NULL,
    error            TEXT,
    machine_id       TEXT    NOT NULL,
    code_version     TEXT    NOT NULL,
    timestamp_utc    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_provider ON attempts (block_id, provider, id);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _b(x: bool | None) -> int | None:
    return None if x is None else int(bool(x))


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- meta ---------------------------------------------------------------
    def set_meta(self, key: str, value: str) -> None:
        """Write-once: refuses to change an existing key to a different value."""
        old = self.get_meta(key)
        if old is not None and old != value:
            raise ValueError(f"meta[{key!r}] already set to {old!r}; refusing to overwrite with {value!r}")
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    # ---- rounds -------------------------------------------------------------
    def start_round_run(self, ctx: RunContext, round_id: int, is_warmup: bool, condition: str,
                        degraded_provider: str | None, strategy_order: list[str]) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(run_no), 0) AS n FROM round_runs WHERE block_id = ? AND round_id = ?",
            (ctx.block_id, round_id),
        ).fetchone()
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO round_runs (block_id, round_id, run_no, is_warmup, condition, degraded_provider,
                       strategy_order, status, started_at, machine_id, code_version, config_hash, is_pilot)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'started', ?, ?, ?, ?, ?)""",
                (ctx.block_id, round_id, row["n"] + 1, int(is_warmup), condition, degraded_provider,
                 json.dumps(strategy_order), utc_now(), ctx.machine_id, ctx.code_version,
                 ctx.config_hash, int(ctx.is_pilot)),
            )
        return cur.lastrowid

    def complete_round_run(self, round_run_id: int) -> None:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE round_runs SET status = 'complete', completed_at = ? WHERE id = ? AND status = 'started'",
                (utc_now(), round_run_id),
            )
        if cur.rowcount != 1:
            raise ValueError(f"round run {round_run_id} not found or already complete")

    def completed_rounds(self, block_id: str) -> set[int]:
        """Round ids of this block that have a complete run (the runner skips these on resume)."""
        rows = self.conn.execute(
            "SELECT DISTINCT round_id FROM round_runs WHERE block_id = ? AND status = 'complete'", (block_id,)
        ).fetchall()
        return {r["round_id"] for r in rows}

    # ---- transfers, attempts, probes ----------------------------------------
    def insert_transfer(self, ctx: RunContext, round_run_id: int, strategy_position: int,
                        decision: Decision, result: UploadResult,
                        probes: list[ProbeResult] = ()) -> int:
        """Insert one transfer with its attempts and probes in a single transaction."""
        rr = self.conn.execute("SELECT * FROM round_runs WHERE id = ?", (round_run_id,)).fetchone()
        if rr is None:
            raise ValueError(f"unknown round run {round_run_id}")
        if rr["block_id"] != ctx.block_id:
            raise ValueError("round run belongs to a different block")
        ts = utc_now()
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO transfers (round_run_id, block_id, round_id, is_warmup, condition, degraded_provider,
                       strategy, strategy_position, first_choice, provider_final, probe_time_s, transfer_time_s,
                       end_to_end_time_s, success, timed_out, n_failed_attempts, retry_time_s, verified,
                       ranking_json, scores_json, weights_json, risk_json, weight_profile,
                       machine_id, code_version, timestamp_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (round_run_id, ctx.block_id, rr["round_id"], rr["is_warmup"], rr["condition"],
                 rr["degraded_provider"], decision.strategy.value, strategy_position, result.first_choice,
                 result.provider, result.probe_time_s, result.transfer_time_s, result.end_to_end_time_s,
                 int(result.success), int(result.timed_out), result.n_failed_attempts, result.retry_time_s,
                 int(result.verified), json.dumps(list(decision.ranking)), json.dumps(decision.scores),
                 json.dumps(decision.weights), json.dumps(decision.risk), decision.profile,
                 ctx.machine_id, ctx.code_version, ts),
            )
            transfer_id = cur.lastrowid
            for i, a in enumerate(result.attempts, start=1):
                self._insert_attempt(ctx, transfer_id, i, decision.strategy.value, a, ts)
            for p in probes:
                self.conn.execute(
                    """INSERT INTO probes (transfer_id, block_id, provider, rtt_ms, throughput_mbps, loss_pct,
                           probe_time_s, ok, error, machine_id, code_version, timestamp_utc)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (transfer_id, ctx.block_id, p.provider, p.rtt_ms, p.throughput_mbps, p.loss_pct,
                     p.probe_time_s, int(p.ok), p.error, ctx.machine_id, ctx.code_version, ts),
                )
        return transfer_id

    def _insert_attempt(self, ctx: RunContext, transfer_id: int, attempt_no: int, strategy: str,
                        a: AttemptResult, ts: str) -> None:
        self.conn.execute(
            """INSERT INTO attempts (transfer_id, attempt_no, block_id, strategy, provider, object_key,
                   transfer_time_s, success, timed_out, verified, verify_time_s, error,
                   machine_id, code_version, timestamp_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (transfer_id, attempt_no, ctx.block_id, strategy, a.provider, a.object_key, a.transfer_time_s,
             int(a.success), int(a.timed_out), _b(a.verified), a.verify_time_s, a.error,
             ctx.machine_id, ctx.code_version, ts),
        )

    def recent_attempts(self, provider: str, limit: int, block_id: str | None = None,
                        strategy: str | None = None) -> list[sqlite3.Row]:
        """Most recent attempts for one provider, newest first (input for the risk score).
        Filter by block (history_scope=block) and/or strategy (history_source=own)."""
        sql = "SELECT * FROM attempts WHERE provider = ?"
        args: list = [provider]
        if block_id is not None:
            sql += " AND block_id = ?"
            args.append(block_id)
        if strategy is not None:
            sql += " AND strategy = ?"
            args.append(strategy)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return self.conn.execute(sql, args).fetchall()

    # ---- history rebuild (resume) -------------------------------------------
    def history_attempts(self, block_id: str | None, strategy: str | None) -> list[AttemptResult]:
        """Attempts from COMPLETE round runs, oldest first, to rebuild the risk history
        when a block is resumed. Attempts of a crashed (incomplete) run are left out,
        as if that run never happened; the round is repeated from scratch."""
        sql = """SELECT a.* FROM attempts a JOIN transfers t ON a.transfer_id = t.id
                 JOIN round_runs r ON t.round_run_id = r.id WHERE r.status = 'complete'"""
        args: list = []
        if block_id is not None:
            sql += " AND a.block_id = ?"
            args.append(block_id)
        if strategy is not None:
            sql += " AND a.strategy = ?"
            args.append(strategy)
        rows = self.conn.execute(sql + " ORDER BY a.id", args).fetchall()
        return [AttemptResult(r["provider"], r["object_key"], r["transfer_time_s"], bool(r["success"]),
                              bool(r["timed_out"]), None if r["verified"] is None else bool(r["verified"]),
                              r["verify_time_s"], r["error"]) for r in rows]

    def history_probes(self, block_id: str | None, strategy: str | None) -> list[ProbeResult]:
        """Probes from COMPLETE round runs, oldest first (see history_attempts)."""
        sql = """SELECT p.* FROM probes p JOIN transfers t ON p.transfer_id = t.id
                 JOIN round_runs r ON t.round_run_id = r.id WHERE r.status = 'complete'"""
        args: list = []
        if block_id is not None:
            sql += " AND p.block_id = ?"
            args.append(block_id)
        if strategy is not None:
            sql += " AND t.strategy = ?"
            args.append(strategy)
        rows = self.conn.execute(sql + " ORDER BY p.id", args).fetchall()
        return [ProbeResult(r["provider"], r["rtt_ms"], r["throughput_mbps"], r["loss_pct"], r["probe_time_s"],
                            bool(r["ok"]), r["error"]) for r in rows]

    def block_machines(self, block_id: str) -> set[str]:
        rows = self.conn.execute("SELECT DISTINCT machine_id FROM round_runs WHERE block_id = ?", (block_id,))
        return {r["machine_id"] for r in rows}

    # ---- backup -------------------------------------------------------------
    def backup(self, backup_dir: str | Path, label: str) -> Path:
        """Copy the database to backup_dir/<db>_<label>_<timestamp>.sqlite. Never overwrites."""
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        dest = backup_dir / f"{self.path.stem}_{label}_{stamp}.sqlite"
        n = 1
        while dest.exists():   # same timestamp (coarse clocks): add a counter, never overwrite
            dest = backup_dir / f"{self.path.stem}_{label}_{stamp}_{n}.sqlite"
            n += 1
        dst = sqlite3.connect(str(dest))
        try:
            self.conn.backup(dst)
        finally:
            dst.close()
        return dest
