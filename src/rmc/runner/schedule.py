"""Generate the full trial schedule in advance, from a recorded random seed.
Operators choose nothing: the runner follows this file.

Blocks (with 3 providers and rounds_per_condition = R, a multiple of 3):
  mild   x each provider degraded   -> 3 blocks of R/3 rounds
  severe x each provider degraded   -> 3 blocks of R/3 rounds
  natural (nothing degraded)        -> 3 blocks of R/3 rounds (same block length)
Block order is shuffled (time-of-day balance) and blocks are numbered B01, B02, ...
in run order. Each block starts with `warmup_rounds_per_block` warm-up rounds
(is_warmup = true), then its measured rounds. Every round lists all strategies
in its own random order.

A round's id is unique within its block; its key for the random strategy is
"<block_id>:R<round_id>".
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

from rmc.config import config_hash, provider_names


def generate_schedule(cfg: dict, seed: int, code_version: str = "unknown") -> dict:
    exp = cfg["experiment"]
    providers = provider_names(cfg)
    n_blocks_per_condition = len(providers)
    rounds_per_condition = exp["rounds_per_condition"]
    if rounds_per_condition % n_blocks_per_condition:
        raise ValueError(f"rounds_per_condition ({rounds_per_condition}) must be a multiple of "
                         f"{n_blocks_per_condition} to balance the degraded-provider rotation")
    rounds_per_block = rounds_per_condition // n_blocks_per_condition
    rng = random.Random(seed)

    specs = []
    for condition in exp["conditions"]:
        for p in providers:
            specs.append({"condition": condition, "degraded_provider": None if condition == "natural" else p})
    rng.shuffle(specs)

    blocks = []
    for i, spec in enumerate(specs, start=1):
        rounds = []
        n_warm = exp["warmup_rounds_per_block"]
        for r in range(1, n_warm + rounds_per_block + 1):
            order = list(exp["strategies"])
            rng.shuffle(order)
            rounds.append({"round_id": r, "is_warmup": r <= n_warm, "strategy_order": order})
        blocks.append({"block_id": f"B{i:02d}", **spec, "rounds": rounds})

    return {
        "seed": seed,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config_hash": config_hash(cfg),
        "code_version": code_version,
        "rounds_per_block": rounds_per_block,
        "blocks": blocks,
    }


def save_schedule(schedule: dict, path: str | Path) -> None:
    """Write once. Refuses to overwrite an existing schedule."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} exists; refusing to overwrite the schedule")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schedule, indent=2), encoding="utf-8")


def load_schedule(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def get_block(schedule: dict, block_id: str) -> dict:
    for b in schedule["blocks"]:
        if b["block_id"] == block_id:
            return b
    raise KeyError(f"block {block_id!r} is not in the schedule")
