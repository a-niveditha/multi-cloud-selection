"""Load trial data for analysis (read-only)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


def open_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Open the database read-only, so analysis can never change trial data."""
    path = Path(db_path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)


def load_transfers(db_path: str | Path, include_pilot: bool = False) -> pd.DataFrame:
    """One row per transfer from COMPLETE round runs only (crashed runs are left out).

    Added column:
      chose_degraded  first choice was the degraded provider (None for natural blocks)
    """
    sql = """SELECT t.*, r.run_no, r.is_pilot, r.config_hash
             FROM transfers t JOIN round_runs r ON t.round_run_id = r.id
             WHERE r.status = 'complete'"""
    if not include_pilot:
        sql += " AND r.is_pilot = 0"
    with open_readonly(db_path) as conn:
        df = pd.read_sql_query(sql + " ORDER BY t.id", conn)
    for col in ("is_warmup", "success", "timed_out", "verified", "is_pilot"):
        df[col] = df[col].astype(bool)
    df["chose_degraded"] = [None if pd.isna(d) else (fc == d)
                            for fc, d in zip(df["first_choice"], df["degraded_provider"])]
    return df


def load_many(db_paths: list[str | Path], include_pilot: bool = False) -> pd.DataFrame:
    """Combine several databases (each block must appear in only one of them).
    Refuses if the same block appears in more than one database (a block run twice)."""
    frames = []
    for p in db_paths:
        df = load_transfers(p, include_pilot)
        df["source_db"] = Path(p).name
        df["id"] = df["id"].astype(str) + "@" + Path(p).name   # transfer ids are only unique per database
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    owners = out.groupby("block_id")["source_db"].nunique()
    dup = sorted(owners[owners > 1].index)
    if dup:
        raise ValueError(f"blocks {dup} appear in more than one database; each block must be run exactly once")
    return out
