"""Load and sanity-check config/config.yaml."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from rmc.models import Condition, Strategy

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "config.yaml"


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict:
    """Load config.yaml."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def config_hash(cfg: dict) -> str:
    """Short hash of the config contents, stored with every block."""
    canonical = json.dumps(cfg, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def provider_names(cfg: dict) -> list[str]:
    return list(cfg["providers"].keys())


def validate_config(cfg: dict) -> list[str]:
    """Return a list of problems (empty list = OK). Does not judge TODO values."""
    problems = []
    names = provider_names(cfg)
    if len(names) != 3:
        problems.append(f"expected 3 providers, got {names}")

    for s in cfg["experiment"]["strategies"]:
        if s not in {x.value for x in Strategy}:
            problems.append(f"unknown strategy {s!r}")
    for c in cfg["experiment"]["conditions"]:
        if c not in {x.value for x in Condition}:
            problems.append(f"unknown condition {c!r}")

    rounds = cfg["experiment"]["rounds_per_condition"]
    if rounds < 8:
        problems.append(f"rounds_per_condition={rounds} is below the required 8")
    if rounds % 3 != 0:
        problems.append(f"rounds_per_condition={rounds} is not a multiple of 3 (unbalanced rotation)")

    for name in names:
        if name not in cfg["cost"]["table"]:
            problems.append(f"no cost entry for provider {name!r}")

    def check_sum(label: str, d: dict) -> None:
        total = sum(d.values())
        if abs(total - 1.0) > 1e-6:
            problems.append(f"{label} sums to {total:.3f}, expected 1.0")

    for profile, w in cfg["weights"]["profiles"].items():
        check_sum(f"weights.profiles.{profile}", w)
    check_sum("network_score.components", cfg["network_score"]["components"])
    check_sum("risk.components", cfg["risk"]["components"])
    return problems
