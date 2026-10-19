"""Pilot check against the REAL buckets (needs credentials in .env).

For each provider: probe it, upload the small probe file under a unique key,
verify by checksum/ETag without downloading, then delete the object.
Not part of the trial data; nothing is written to the database.

    python scripts/smoke_cloud.py              # all providers
    python scripts/smoke_cloud.py --only gcs
    python scripts/smoke_cloud.py --rtt-only   # TCP-connect RTT/loss only, no credentials needed
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rmc.config import REPO_ROOT, load_config  # noqa: E402
from rmc.models import LocalFile  # noqa: E402
from rmc.probing.probes import resolve_ipv4, summarise, tcp_connect_ms  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="one provider name")
    ap.add_argument("--rtt-only", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    names = [args.only] if args.only else list(cfg["providers"])
    pc = cfg["probe"]

    for n in names:
        host = cfg["providers"][n]["endpoint_host"]
        ip = resolve_ipv4(host, pc["port"])
        rtt, loss = summarise([tcp_connect_ms(ip, pc["port"], pc["sample_timeout_s"]) for _ in range(pc["samples"])])
        print(f"{n:8s} {host} ({ip})  rtt_median={rtt}  loss={loss:.0f}%")
    if args.rtt_only:
        return

    from rmc.probing.probes import NetworkProber
    from rmc.providers.factory import build_providers

    providers = build_providers(cfg)
    prober = NetworkProber(cfg, providers)
    f = LocalFile.from_path(REPO_ROOT / pc["payload_path"])
    for n in names:
        print(f"\n[{n}] probe: {prober.probe(n)}")
        key = f"{cfg['transfer']['object_prefix']}/smoke/{int(time.time())}-{n}.bin"
        r = providers[n].upload(f, key, cfg["transfer"]["timeout_s"])
        print(f"[{n}] upload: {r}")
        if r.success:
            providers[n].delete_object(key)
            print(f"[{n}] deleted {key}")


if __name__ == "__main__":
    main()
