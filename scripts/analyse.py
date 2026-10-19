"""Paired analysis and plots. Reads the database READ-ONLY.

    python scripts/analyse.py                                   # real data (data/trials.sqlite)
    python scripts/analyse.py --db data/mock.sqlite --include-pilot --out data/analysis_mock
    python scripts/analyse.py --db collected/alice.sqlite collected/bob.sqlite   # several trial machines

Writes CSV tables, PNG figures and summary.txt to --out (default data/analysis/<timestamp>).
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from rmc.analysis import plots, stats  # noqa: E402
from rmc.analysis.load import load_many  # noqa: E402
from rmc.config import REPO_ROOT, load_config  # noqa: E402


def run(dbs: list[Path], out: Path, acfg: dict, include_pilot: bool) -> Path:
    df = load_many(dbs, include_pilot=include_pilot)
    if df.empty:
        raise SystemExit("no complete rounds found (pilot/mock data needs --include-pilot)")
    out.mkdir(parents=True, exist_ok=True)
    metric = acfg["metric"]

    tables = {
        "comparisons": stats.compare_all(df, acfg),
        "variability": stats.variability(df, metric),
        "reliability": stats.reliability(df),
        "degraded_choice_pairs": stats.degraded_choice_pairs(df),
        "probe_overhead": stats.probe_overhead(df),
        "cold_start": stats.cold_start(df, metric),
    }
    for name, t in tables.items():
        t.to_csv(out / f"{name}.csv", index=False)
    figures = plots.make_all(df, acfg, out)

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    m = stats.measured(df)
    lines = [
        f"Analysis of {[str(d) for d in dbs]}  ({datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC})",
        f"Transfers: {len(df)} ({len(m)} measured, {len(df) - len(m)} warm-up); "
        f"blocks: {df['block_id'].nunique()}; machines: {sorted(df['machine_id'].unique())}; "
        f"code versions: {sorted(df['code_version'].unique())}; config hashes: {sorted(df['config_hash'].unique())}",
        f"Metric: {metric}. diff = baseline - proposed (positive = proposed faster). Two-sided tests.",
        f"Primary test: {acfg['primary_test']}, correction: {acfg['multiple_comparison']}, "
        f"alpha {acfg['alpha']}, failed transfers: {acfg['failed_transfer_policy']}.",
        "Exploratory subgroup rows are NOT corrected for multiple comparisons.",
        "",
    ]
    for name, t in tables.items():
        lines += [f"== {name} ==", t.round(4).to_string(index=False), ""]
    lines += ["Figures: " + ", ".join(p.name for p in figures)]
    summary = out / "summary.txt"
    summary.write_text("\n".join(lines), encoding="utf-8")
    return summary


def main() -> None:
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", nargs="+", default=[str(REPO_ROOT / cfg["storage"]["db_path"])],
                    help="one or more databases (one per trial machine)")
    ap.add_argument("--out")
    ap.add_argument("--include-pilot", action="store_true", help="include pilot/mock rows (never for the report)")
    args = ap.parse_args()
    out = Path(args.out) if args.out else REPO_ROOT / "data" / "analysis" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    summary = run([Path(d) for d in args.db], out, cfg["analysis"], args.include_pilot)
    print(summary.read_text(encoding="utf-8"))
    print(f"\nWritten to {out}")


if __name__ == "__main__":
    main()
