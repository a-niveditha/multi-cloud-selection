"""Export a read-only copy of the trial data for analysis and sharing:
a SQLite snapshot plus one CSV per table, in data/export/<timestamp>/.
The live database is opened read-only and never changed.

    python scripts/export_data.py
"""
import argparse
import os
import sqlite3
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from rmc.analysis.load import open_readonly  # noqa: E402
from rmc.config import REPO_ROOT, load_config  # noqa: E402

TABLES = ["meta", "round_runs", "transfers", "attempts", "probes"]


def main() -> None:
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO_ROOT / cfg["storage"]["db_path"]))
    args = ap.parse_args()

    out = REPO_ROOT / "data" / "export" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=False)          # never reuse an existing export
    src = open_readonly(args.db)
    snapshot = out / Path(args.db).name
    dst = sqlite3.connect(str(snapshot))
    src.backup(dst)
    dst.close()
    for t in TABLES:
        pd.read_sql_query(f"SELECT * FROM {t}", src).to_csv(out / f"{t}.csv", index=False)
    src.close()
    for f in out.iterdir():
        os.chmod(f, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)   # read-only files
    print(f"Exported {Path(args.db).name} and {len(TABLES)} CSV tables to {out}")


if __name__ == "__main__":
    main()
