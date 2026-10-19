"""Measure the natural-network baseline and the nearest-region order, ONCE per
trial machine, with NO netem rules. Writes storage.calibration_path; refuses to overwrite.

    python scripts/calibrate.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rmc.config import REPO_ROOT, load_config  # noqa: E402
from rmc.netem.netem import TcNetem, netem_interface  # noqa: E402
from rmc.probing.probes import NetworkProber, calibrate  # noqa: E402
from rmc.providers.factory import build_providers  # noqa: E402
from rmc.runinfo import machine_id  # noqa: E402
from rmc.runner.setup_file import build_setup, save_setup  # noqa: E402


def main() -> None:
    cfg = load_config()
    out = REPO_ROOT / cfg["storage"]["calibration_path"]
    if out.exists():
        sys.exit(f"{out} already exists; delete it deliberately if you really want to recalibrate.")

    n = cfg["netem"]
    netem = TcNetem(netem_interface(cfg), n["targets"], {}, dry_run=n["dry_run"], sudo=n["sudo"])
    if netem.has_rules():
        sys.exit("netem rules are present; calibration must be done on the natural network. Clear them first.")

    providers = build_providers(cfg)
    names = list(cfg["providers"])
    samples = cfg["probe"]["calibration_samples"]
    print(f"Calibrating {names} with {samples} probes each ...")
    baseline = calibrate(NetworkProber(cfg, providers), names, samples)
    setup = build_setup(cfg, baseline, machine_id())
    save_setup(setup, out)

    for p, v in baseline.items():
        print(f"  {p:8s} rtt={v['rtt_ms']:.1f} ms  loss={v['loss_pct']:.1f} %  thr={v['throughput_mbps']:.2f} Mbit/s"
              f"  ({v['n_ok']}/{v['n']} ok)")
    print("Nearest order:", setup["nearest_order"])
    print(f"Written to {out}")


if __name__ == "__main__":
    main()
