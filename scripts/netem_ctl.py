"""Manual netem control for the operator (show / clear / apply).
Uses netem.dry_run from config unless --execute or --dry-run is given.

    python scripts/netem_ctl.py show
    python scripts/netem_ctl.py clear --execute
    python scripts/netem_ctl.py apply --provider gcs --condition mild            # dry-run: print only
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rmc.config import REPO_ROOT, load_config  # noqa: E402
from rmc.netem.netem import TcNetem, netem_interface, uncovered_endpoint_ips  # noqa: E402
from rmc.runner.setup_file import load_setup  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["show", "clear", "apply", "check-targets"])
    ap.add_argument("--provider")
    ap.add_argument("--condition", choices=["mild", "severe"])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="really run the commands")
    mode.add_argument("--dry-run", action="store_true", help="only print the commands")
    args = ap.parse_args()

    cfg = load_config()
    n = cfg["netem"]
    dry = True if args.dry_run else (False if args.execute else n["dry_run"])

    if args.action == "check-targets":
        for p, pcfg in cfg["providers"].items():
            bad = uncovered_endpoint_ips(pcfg["endpoint_host"], n["targets"][p], cfg["probe"]["port"])
            print(f"{p:8s} {'OK' if not bad else 'NOT COVERED: ' + ', '.join(bad)}")
        return

    baseline_rtt = {}
    if args.action == "apply":
        if not (args.provider and args.condition):
            sys.exit("apply needs --provider and --condition")
        baseline_rtt = {p: v["rtt_ms"] for p, v in load_setup(REPO_ROOT / cfg["storage"]["calibration_path"])["baseline"].items()}
    netem = TcNetem(netem_interface(cfg), n["targets"], baseline_rtt, dry_run=dry, sudo=n["sudo"])

    if args.action == "show":
        print(netem.show() or "(dry-run: nothing shown)")
    elif args.action == "clear":
        netem.clear()
        print("cleared" + (" (dry-run)" if dry else ""))
    else:
        c = n["conditions"][args.condition]
        netem.apply(args.provider, c["rtt_increase_ratio"], c["loss_pct"])
        print(f"applied {args.condition} to {args.provider}" + (" (dry-run)" if dry else ""))


if __name__ == "__main__":
    main()
