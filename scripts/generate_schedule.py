"""Generate the trial schedule ONCE, from a recorded seed. Refuses to overwrite.

    python scripts/generate_schedule.py --seed 42
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rmc.config import REPO_ROOT, load_config  # noqa: E402
from rmc.runinfo import code_version  # noqa: E402
from rmc.runner.schedule import generate_schedule, save_schedule  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True, help="record this seed in the report")
    ap.add_argument("--out", help="default: storage.schedule_path from config")
    args = ap.parse_args()

    cfg = load_config()
    out = Path(args.out) if args.out else REPO_ROOT / cfg["storage"]["schedule_path"]
    schedule = generate_schedule(cfg, args.seed, code_version())
    save_schedule(schedule, out)

    print(f"Schedule written to {out} (seed {args.seed}, config {schedule['config_hash']})")
    for b in schedule["blocks"]:
        n_warm = sum(r["is_warmup"] for r in b["rounds"])
        print(f"  {b['block_id']}  {b['condition']:8s} degraded={b['degraded_provider'] or '-':7s} "
              f"rounds={len(b['rounds']) - n_warm} (+{n_warm} warm-up)")
    print("Blocks per condition:", dict(Counter(b["condition"] for b in schedule["blocks"])))


if __name__ == "__main__":
    main()
