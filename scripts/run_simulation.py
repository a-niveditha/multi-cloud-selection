"""Run the whole experiment in a SIMULATED multi-cloud environment, then analyse it.

The three providers are MockProvider objects configured under `simulation:` in
config.yaml, the mild/severe conditions are applied to the simulated paths, and time
is simulated, so a full run takes seconds on any OS. The selection strategies, risk
history, failover, database and analysis are the real experiment code.

    python scripts/run_simulation.py                          # seed from config
    python scripts/run_simulation.py --seed 7 --rounds-per-condition 30
    python scripts/run_simulation.py --overwrite              # replace an earlier run with the same seed
    python scripts/run_simulation.py --name light --set probe.samples=3 --set probe.max_age_s=60

--set overrides one config value for this run only (value parsed as YAML); the
overrides are printed and written at the top of the analysis summary.

Writes results/<name, default seed<N>>/ : simulation.sqlite, schedule.json, analysis/
(per-block database backups go to data/backups/<name>/, which is not committed).
Every result from here is a SIMULATION result and must be reported as such.
"""
import argparse
import shutil
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyse  # noqa: E402
from make_payload import make  # noqa: E402
from rmc.clock import SimClock  # noqa: E402
from rmc.config import REPO_ROOT, load_config  # noqa: E402
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
    ap.add_argument("--set", action="append", default=[], metavar="KEY.PATH=VALUE",
                    help="override a config value for this run, e.g. probe.max_age_s=60")
    ap.add_argument("--name", help="output folder name (default seed<N>)")
    args = ap.parse_args()

    cfg["experiment"]["rounds_per_condition"] = args.rounds_per_condition
    for item in args.set:
        key, _, value = item.partition("=")
        *parents, leaf = key.split(".")
        node = cfg
        for k in parents:
            node = node[k]
        if leaf not in node:
            sys.exit(f"--set {key}: no such config key")
        node[leaf] = yaml.safe_load(value)
    note = NOTE + (f"\nConfig overrides: {', '.join(args.set)}" if args.set else "")
    name = args.name or f"seed{args.seed}"
    out = REPO_ROOT / "results" / name
    if out.exists():
        if not args.overwrite:
            sys.exit(f"{out} already exists; use --overwrite, another --name or another --seed")
        shutil.rmtree(out)
    out.mkdir(parents=True)

    exp = cfg["experiment"]
    make(REPO_ROOT / exp["payload_path"], exp["payload_size_bytes"], exp["payload_seed"])
    payload = LocalFile.from_path(REPO_ROOT / exp["payload_path"])

    clock = SimClock()
    providers = MockProvider.from_config(cfg, clock, section="simulation", seed=args.seed)
    prober = MockProber(providers, cfg["probe"]["timeout_s"], pcfg=cfg["probe"])
    netem = MockNetem(providers)
    setup = build_setup(cfg, calibrate(prober, list(providers), cfg["probe"]["calibration_samples"]), "simulation")
    schedule = generate_schedule(cfg, args.seed, code_version())
    cfg_hash = schedule["config_hash"]   # before the run-specific backup path is set below
    cfg["storage"]["backup_dir"] = str(REPO_ROOT / "data" / "backups" / name)
    save_schedule(schedule, out / "schedule.json")

    print(note)
    print(f"Seed {args.seed}, {args.rounds_per_condition} rounds per condition, "
          f"{len(schedule['blocks'])} blocks. Simulated baseline:")
    for p, v in setup["baseline"].items():
        print(f"  {p:8s} rtt={v['rtt_ms']:.1f} ms  loss={v['loss_pct']:.2f} %  thr={v['throughput_mbps']:.1f} Mbit/s")

    log = print if args.verbose else (lambda *a, **k: None)
    db = Database(out / "simulation.sqlite")
    try:
        for b in schedule["blocks"]:
            ctx = RunContext(machine_id="simulation", code_version=code_version(), block_id=b["block_id"],
                             config_hash=cfg_hash, is_pilot=True)
            preflight(cfg, schedule, b["block_id"], db, ctx, setup,
                      git_dirty=False, allow_dirty=True)
            s = BlockRunner(cfg, schedule, b["block_id"], db, ctx, providers, prober, netem, setup,
                            payload, clock, log=log).run()
            print(f"  {b['block_id']} {b['condition']:8s} degraded={b['degraded_provider'] or '-':7s} "
                  f"{s['transfers']} transfers")
    except RunnerRefused as e:
        sys.exit(f"REFUSED: {e}")
    finally:
        db.close()

    summary = analyse.run([out / "simulation.sqlite"], out / "analysis", cfg["analysis"],
                          include_pilot=True, note=note)
    print()
    print(summary.read_text(encoding="utf-8"))
    print(f"\nWritten to {out}")


if __name__ == "__main__":
    main()
