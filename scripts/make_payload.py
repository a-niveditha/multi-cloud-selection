"""Create the reproducible 10 MB trial payload from config (size and seed).
run_simulation.py calls this automatically.

    python scripts/make_payload.py
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rmc.config import REPO_ROOT, load_config  # noqa: E402
from rmc.models import LocalFile  # noqa: E402


def make(path: Path, size: int, seed: int) -> None:
    if path.exists():
        print(f"{path} already exists; not overwriting.")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(random.Random(seed).randbytes(size))
    f = LocalFile.from_path(path)
    print(f"{f.path}  {f.size_bytes} bytes  md5={f.md5_hex}  sha1={f.sha1_hex}")


def main() -> None:
    cfg = load_config()
    exp = cfg["experiment"]
    make(REPO_ROOT / exp["payload_path"], exp["payload_size_bytes"], exp["payload_seed"])


if __name__ == "__main__":
    main()
