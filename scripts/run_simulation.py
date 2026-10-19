"""Run the whole experiment in a SIMULATED multi-cloud environment, then analyse it.

No cloud accounts, credentials, Ubuntu or netem are needed: the three providers are
MockProvider objects configured under `simulation:` in config.yaml, the mild/severe
conditions are applied to the simulated paths, and time is simulated (a full run
takes seconds). The selection strategies, risk history, failover, database and
analysis are the same code as in the real trials.

    python scripts/run_simulation.py                          # seed from config
    python scripts/run_simulation.py --seed 7 --rounds-per-condition 30
    python scripts/run_simulation.py --overwrite              # replace an earlier run with the same seed

Writes data/simulation/seed<N>/ : simulation.sqlite, schedule.json, backups/, analysis/.
Every result from here is a SIMULATION result and must be reported as such.
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyse  # noqa: E402
from make_payload import make  # noqa: E402
from rmc.clock import SimClock  # noqa: E402
from rmc.config import REPO_ROOT, config_hash, load_config  # noqa: E402
from rmc.db import Database  # noqa: E402
from rmc.models import LocalFile, RunContext  # noqa: E402
from rmc.netem.netem import MockNetem  # noqa: E402
from rmc.probing.probes import MockProber, calibrate  # noqa: E402
from rmc.providers.mock import MockProvider  # noqa: E402
from rmc.runinfo import code_version  # noqa: E402
from rmc.runner.runner import BlockRunner, RunnerRefused, preflight  # noqa: E402
from rmc.runner.schedule import generate_schedule, save_schedule  # noqa: E402
from rmc.runner.setup_file import build_setup  # noqa: E402

NOTE = ("DATA SOURCE: SIMULATION. Providers, network conditions and failures are simulated "
        "(config.yaml `simulation:` profiles); these are not real cloud measurements.")


def main() -> None:
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=cfg["simulation"]["seed"],
                    help="seeds the providers and the schedule; record it in the report")
    ap.add_argument("--rounds-per-condition", type=int, default=cfg["experiment"]["rounds_per_condition"],
                    help="measured paired rounds per condition (multiple of 3)")
    ap.add_argument("--overwrite", action="store_true", help="delete an earlier run with the same seed first")
    ap.add_argument("--verbose", action="store_true", help="print every transfer")
    args = ap.parse_args()

    cfg["experiment"]["rounds_per_condition"] = args.rounds_per_condition
    out = REPO_ROOT / "data" / "simulation" / f"seed{args.seed}"
    if out.exists():
        if not args.overwrite:
            sys.exit(f"{out} already exists; use --overwrite or another --seed")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    cfg["storage"]["backup_dir"] = str(out / "backups")

    exp = cfg["experiment"]
    make(REPO_ROOT / exp["payload_path"], exp["payload_size_bytes"], exp["payload_seed"])
    payload = LocalFile.from_path(REPO_ROOT / exp["payload_path"])

    clock = SimClock()
    providers = MockProvider.from_config(cfg, clock, section="simulation", seed=args.seed)
    prober = MockProber(providers, cfg["probe"]["timeout_s"])
    netem = MockNetem(providers)
    setup = build_setup(cfg, calibrate(prober, list(providers), cfg["probe"]["calibration_samples"]), "simulation")
    schedule = generate_schedule(cfg, args.seed, code_version())
    save_schedule(schedule, out / "schedule.json")

    print(NOTE)
    print(f"Seed {args.seed}, {args.rounds_per_condition} rounds per condition, "
          f"{len(schedule['blocks'])} blocks. Simulated baseline:")
    for p, v in setup["baseline"].items():
        print(f"  {p:8s} rtt={v['rtt_ms']:.1f} ms  loss={v['loss_pct']:.2f} %  thr={v['throughput_mbps']:.1f} Mbit/s")

    log = print if args.verbose else (lambda *a, **k: None)
    db = Database(out / "simulation.sqlite")
    try:
        for b in schedule["blocks"]:
            ctx = RunContext(machine_id="simulation", code_version=code_version(), block_id=b["block_id"],
                             config_hash=config_hash(cfg), is_pilot=True)
            preflight(cfg, schedule, b["block_id"], db, ctx, setup,
                      git_dirty=False, allow_dirty=True, netem_dry_run=False)
            s = BlockRunner(cfg, schedule, b["block_id"], db, ctx, providers, prober, netem, setup,
                            payload, clock, log=log).run()
            print(f"  {b['block_id']} {b['condition']:8s} degraded={b['degraded_provider'] or '-':7s} "
                  f"{s['transfers']} transfers")
    except RunnerRefused as e:
        sys.exit(f"REFUSED: {e}")
    finally:
        db.close()

    summary = analyse.run([out / "simulation.sqlite"], out / "analysis", cfg["analysis"],
                          include_pilot=True, note=NOTE)
    print()
    print(summary.read_text(encoding="utf-8"))
    print(f"\nWritten to {out}")


if __name__ == "__main__":
    main()
