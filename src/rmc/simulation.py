"""Run the whole experiment in the simulated multi-cloud environment.

Used by scripts/run_simulation.py and by the dashboard's What-if tab:

    run = simulate(cfg, seed=2026, out_dir=Path("results/x"), backup_dir=Path("data/backups/x"))
    run.db_path  -> simulation.sqlite (analyse with rmc.analysis, include_pilot=True)

Writes to out_dir: simulation.sqlite, schedule.json, setup.json (the natural
baseline and the nearest-region order the strategies used) and config.json (the
effective configuration of this run, after overrides).
"""
from __future__ import annotations

import copy
import json
import random
from dataclasses import dataclass
from pathlib import Path

import yaml

from rmc.clock import SimClock
from rmc.config import REPO_ROOT
from rmc.db import Database
from rmc.models import LocalFile, RunContext
from rmc.netem.netem import MockNetem
from rmc.probing.probes import MockProber, calibrate
from rmc.providers.mock import MockProvider
from rmc.runinfo import code_version
from rmc.runner.runner import BlockRunner, preflight
from rmc.runner.schedule import generate_schedule, save_schedule
from rmc.runner.setup_file import build_setup

NOTE = ("DATA SOURCE: SIMULATION. Providers, network conditions and failures are simulated "
        "(config.yaml `simulation:` profiles); these are not real cloud measurements.")


@dataclass
class SimulationRun:
    db_path: Path
    schedule: dict
    setup: dict


def apply_overrides(cfg: dict, items: list[str]) -> dict:
    """A copy of cfg with "key.path=value" overrides applied (value parsed as YAML)."""
    cfg = copy.deepcopy(cfg)
    for item in items:
        key, _, value = item.partition("=")
        *parents, leaf = key.split(".")
        node = cfg
        for k in parents:
            node = node[k]
        if leaf not in node:
            raise KeyError(f"no such config key: {key}")
        node[leaf] = yaml.safe_load(value)
    return cfg


def make_payload(path: Path, size: int, seed: int) -> LocalFile:
    """Create the reproducible payload file if it does not exist yet."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(random.Random(seed).randbytes(size))
    return LocalFile.from_path(path)


def simulate(cfg: dict, seed: int, out_dir: Path, backup_dir: Path, log=None, on_block=None) -> SimulationRun:
    """Calibrate, schedule and run every block. `out_dir` must not contain a previous run.
    on_block(i, n_blocks, block, summary) is called after each block (for progress)."""
    cfg = copy.deepcopy(cfg)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    exp = cfg["experiment"]
    payload = make_payload(REPO_ROOT / exp["payload_path"], exp["payload_size_bytes"], exp["payload_seed"])

    clock = SimClock()
    providers = MockProvider.from_config(cfg, clock, section="simulation", seed=seed)
    prober = MockProber(providers, cfg["probe"]["timeout_s"], pcfg=cfg["probe"])
    netem = MockNetem(providers)
    setup = build_setup(cfg, calibrate(prober, list(providers), cfg["probe"]["calibration_samples"]), "simulation")
    version = code_version()
    schedule = generate_schedule(cfg, seed, version)
    cfg_hash = schedule["config_hash"]   # before the run-specific backup path is set below
    save_schedule(schedule, out_dir / "schedule.json")
    (out_dir / "setup.json").write_text(json.dumps(setup, indent=2), encoding="utf-8")
    (out_dir / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    cfg["storage"]["backup_dir"] = str(backup_dir)

    db_path = out_dir / "simulation.sqlite"
    db = Database(db_path)
    try:
        for i, b in enumerate(schedule["blocks"]):
            ctx = RunContext(machine_id="simulation", code_version=version, block_id=b["block_id"],
                             config_hash=cfg_hash, is_pilot=True)
            preflight(cfg, schedule, b["block_id"], db, ctx, setup, git_dirty=False, allow_dirty=True)
            summary = BlockRunner(cfg, schedule, b["block_id"], db, ctx, providers, prober, netem, setup,
                                  payload, clock, log=log or (lambda *a, **k: None)).run()
            if on_block:
                on_block(i, len(schedule["blocks"]), b, summary)
    finally:
        db.close()
    return SimulationRun(db_path=db_path, schedule=schedule, setup=setup)
