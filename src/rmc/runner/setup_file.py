"""The setup file (data/calibration.json), written ONCE per trial machine before
the real trials, with no netem rules active:

  baseline       natural median RTT / loss / throughput per provider (probing.calibrate)
                 -> used by the weight rule (RTT increase) and to size netem delays
  nearest_order  closest provider first -> the fixed-nearest-region strategy
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from rmc.selection.strategies import nearest_order


def build_setup(cfg: dict, baseline: dict, machine_id: str) -> dict:
    rtts = {p: v["rtt_ms"] for p, v in baseline.items()}
    return {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "machine_id": machine_id,
        "baseline": baseline,
        "nearest_method": cfg["nearest_region"]["method"],
        "nearest_order": nearest_order(cfg, rtts),
    }


def save_setup(setup: dict, path: str | Path) -> None:
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} exists; refusing to overwrite (delete it deliberately to recalibrate)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(setup, indent=2), encoding="utf-8")


def load_setup(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run scripts/calibrate.py first")
    return json.loads(path.read_text(encoding="utf-8"))
