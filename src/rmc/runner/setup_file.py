"""The setup of a run, measured once at the start with no degradation:

  baseline       natural median RTT / loss / throughput per provider (probing.calibrate)
                 -> used by the weight rule (RTT increase) and to size the degradation
  nearest_order  closest provider first -> the fixed-nearest-region strategy
"""
from __future__ import annotations

from datetime import datetime, timezone

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
