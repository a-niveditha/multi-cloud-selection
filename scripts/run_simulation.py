"""Run the whole experiment in a SIMULATED multi-cloud environment, then analyse it.

The three providers are MockProvider objects configured under `simulation:` in
config.yaml, the mild/severe conditions are applied to the simulated paths, and time
is simulated, so a full run takes seconds on any OS. The selection strategies, risk
history, failover, database and analysis are the real experiment code (rmc.simulation).

    python scripts/run_simulation.py                          # seed from config
    python scripts/run_simulation.py --seed 7 --rounds-per-condition 30
    python scripts/run_simulation.py --overwrite              # replace an earlier run with the same seed
    python scripts/run_simulation.py --name light --set probe.samples=3 --set probe.max_age_s=60

--set overrides one config value for this run only (value parsed as YAML); the
overrides are printed and written at the top of the analysis summary.

Writes results/<name, default seed<N>>/ : simulation.sqlite, schedule.json, setup.json,
analysis/ (per-block database backups go to data/backups/<name>/, which is not committed).
Every result from here is a SIMULATION result and must be reported as such.
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyse  # noqa: E402
from rmc.config import REPO_ROOT, load_config  # noqa: E402
from rmc.runner.runner import RunnerRefused  # noqa: E402
from rmc.simulation import NOTE, apply_overrides, simulate  # noqa: E402


def main() -> None:
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=cfg["simulation"]["seed"],
                    help="seeds the providers and the schedule; record it in the report")
    ap.add_argument("--rounds-per-condition", type=int, default=cfg["experiment"]["rounds_per_condition"],
                    help="measured paired rounds per condition (multiple of 3)")
    ap.add_argument("--overwrite", action="store_true", help="delete an earlier run with the same seed first")
    ap.add_argument("--verbose", action="store_true", help="print every transfer")
    ap.add_argument("--set", action="append", default=[], metavar="KEY.PATH=VALUE",
                    help="override a config value for this run, e.g. probe.max_age_s=60")
    ap.add_argument("--name", help="output folder name (default seed<N>)")
    args = ap.parse_args()

    cfg["experiment"]["rounds_per_condition"] = args.rounds_per_condition
    try:
        cfg = apply_overrides(cfg, args.set)
    except KeyError as e:
        sys.exit(f"--set: {e}")
    note = NOTE + (f"\nConfig overrides: {', '.join(args.set)}" if args.set else "")
    name = args.name or f"seed{args.seed}"
    out = REPO_ROOT / "results" / name
    if out.exists():
        if not args.overwrite:
            sys.exit(f"{out} already exists; use --overwrite, another --name or another --seed")
        shutil.rmtree(out)

    print(note)

    def progress(i, n, b, s):
        print(f"  {b['block_id']} {b['condition']:8s} degraded={b['degraded_provider'] or '-':7s} "
              f"{s['transfers']} transfers")

    try:
        run = simulate(cfg, args.seed, out, REPO_ROOT / "data" / "backups" / name,
                       log=print if args.verbose else None, on_block=progress)
    except RunnerRefused as e:
        sys.exit(f"REFUSED: {e}")

    print(f"Seed {args.seed}, {args.rounds_per_condition} rounds per condition. Simulated baseline:")
    for p, v in run.setup["baseline"].items():
        print(f"  {p:8s} rtt={v['rtt_ms']:.1f} ms  loss={v['loss_pct']:.2f} %  thr={v['throughput_mbps']:.1f} Mbit/s")

    summary = analyse.run([run.db_path], out / "analysis", cfg["analysis"], include_pilot=True, note=note)
    print()
    print(summary.read_text(encoding="utf-8"))
    print(f"\nWritten to {out}")


if __name__ == "__main__":
    main()
