"""Create the reproducible 10 MB trial payload from config (size and seed).
run_simulation.py calls this automatically.

    python scripts/make_payload.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rmc.config import REPO_ROOT, load_config  # noqa: E402
from rmc.simulation import make_payload  # noqa: E402


def main() -> None:
    exp = load_config()["experiment"]
    f = make_payload(REPO_ROOT / exp["payload_path"], exp["payload_size_bytes"], exp["payload_seed"])
    print(f"{f.path}  {f.size_bytes} bytes  md5={f.md5_hex}  sha1={f.sha1_hex}")


if __name__ == "__main__":
    main()
