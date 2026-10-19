"""Run one block of the schedule. The schedule decides everything.

    python scripts/run_block.py --block B01            # real trial
    python scripts/run_block.py --block B01 --pilot    # pilot: separate database, not real data
    python scripts/run_block.py --block B01 --mock     # development: mock providers, simulated time

Rerunning the same command after a crash resumes the block (completed rounds are skipped).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rmc.clock import RealClock, SimClock  # noqa: E402
from rmc.config import REPO_ROOT, config_hash, load_config  # noqa: E402
from rmc.db import Database  # noqa: E402
from rmc.models import LocalFile, RunContext  # noqa: E402
from rmc.runinfo import code_version, git_dirty_or_none, machine_id  # noqa: E402
from rmc.runner.runner import BlockRunner, RunnerRefused, preflight  # noqa: E402
from rmc.runner.schedule import generate_schedule, get_block, load_schedule, save_schedule  # noqa: E402


def build_mock(cfg, schedule_seed):
    from rmc.netem.netem import MockNetem
    from rmc.probing.probes import MockProber, calibrate
    from rmc.providers.mock import MockProvider
    from rmc.runner.setup_file import build_setup

    clock = SimClock()
    mocks = MockProvider.from_config(cfg, clock)
    prober = MockProber(mocks, cfg["probe"]["timeout_s"])
    setup = build_setup(cfg, calibrate(prober, list(mocks), 10), "mock")
    # The mock schedule is saved once (next to the mock DB) so --mock runs can be resumed too.
    sched_path = REPO_ROOT / cfg["storage"]["mock_db_path"].replace(".sqlite", "_schedule.json")
    if not sched_path.exists():
        save_schedule(generate_schedule(cfg, schedule_seed, "mock"), sched_path)
    schedule = load_schedule(sched_path)
    return dict(providers=mocks, prober=prober, netem=MockNetem(mocks), setup=setup, clock=clock,
                schedule=schedule, db_path=cfg["storage"]["mock_db_path"])


def build_real(cfg, pilot):
    from rmc.netem.netem import TcNetem, netem_interface, uncovered_endpoint_ips
    from rmc.probing.probes import NetworkProber
    from rmc.providers.factory import build_providers
    from rmc.runner.setup_file import load_setup

    setup = load_setup(REPO_ROOT / cfg["storage"]["calibration_path"])
    n = cfg["netem"]
    netem = TcNetem(netem_interface(cfg), n["targets"], {p: v["rtt_ms"] for p, v in setup["baseline"].items()},
                    dry_run=n["dry_run"], sudo=n["sudo"])
    providers = build_providers(cfg)
    return dict(providers=providers, prober=NetworkProber(cfg, providers), netem=netem, setup=setup,
                clock=RealClock(), schedule=load_schedule(REPO_ROOT / cfg["storage"]["schedule_path"]),
                db_path=cfg["storage"]["pilot_db_path" if pilot else "db_path"],
                check_targets=lambda p: uncovered_endpoint_ips(cfg["providers"][p]["endpoint_host"],
                                                               n["targets"][p], cfg["probe"]["port"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--block", required=True)
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--mock-seed", type=int, default=0, help="schedule seed for --mock runs")
    ap.add_argument("--allow-dirty", action="store_true", help="DEVELOPMENT ONLY: allow uncommitted changes")
    args = ap.parse_args()

    cfg = load_config()
    try:
        parts = build_mock(cfg, args.mock_seed) if args.mock else build_real(cfg, args.pilot)
    except FileNotFoundError as e:
        sys.exit(f"REFUSED: {e}")
    payload = LocalFile.from_path(REPO_ROOT / cfg["experiment"]["payload_path"])
    if payload.size_bytes != cfg["experiment"]["payload_size_bytes"]:
        sys.exit(f"payload is {payload.size_bytes} bytes, config says {cfg['experiment']['payload_size_bytes']}")

    ctx = RunContext(machine_id=machine_id(), code_version=code_version(), block_id=args.block,
                     config_hash=config_hash(cfg), is_pilot=args.pilot or args.mock)
    db = Database(REPO_ROOT / parts["db_path"])
    try:
        block = get_block(parts["schedule"], args.block)
        if not args.mock and block["degraded_provider"]:
            bad = parts["check_targets"](block["degraded_provider"])
            if bad:
                raise RunnerRefused(f"endpoint IPs {bad} of {block['degraded_provider']} are not covered by "
                                    "netem.targets; netem would not affect them")
        preflight(cfg, parts["schedule"], args.block, db, ctx, parts["setup"],
                  git_dirty=git_dirty_or_none(), allow_dirty=args.allow_dirty or args.mock,
                  netem_dry_run=getattr(parts["netem"], "dry_run", False))
        runner = BlockRunner(cfg, parts["schedule"], args.block, db, ctx, parts["providers"], parts["prober"],
                             parts["netem"], parts["setup"], payload, parts["clock"])
        print("Done:", runner.run())
    except RunnerRefused as e:
        sys.exit(f"REFUSED: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
