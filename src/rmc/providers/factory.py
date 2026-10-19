"""Build the provider objects from config: real cloud adapters or mocks."""
from __future__ import annotations

from rmc.config import REPO_ROOT
from rmc.providers.base import StorageProvider
from rmc.providers.mock import MockProvider


def build_providers(cfg: dict, mock: bool = False, clock=None) -> dict[str, StorageProvider]:
    """{name: provider} in config order. Real adapters read credentials from the
    environment (and from .env if present); they are imported only when needed."""
    if mock:
        return MockProvider.from_config(cfg, clock)

    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")

    from rmc.providers.b2 import B2Provider
    from rmc.providers.gcs import GCSProvider
    from rmc.providers.s3 import S3Provider
    kinds = {"s3": S3Provider, "gcs": GCSProvider, "b2": B2Provider}

    out = {}
    for name, pcfg in cfg["providers"].items():
        if pcfg["kind"] not in kinds:
            raise ValueError(f"provider {name}: unknown kind {pcfg['kind']!r}")
        out[name] = kinds[pcfg["kind"]].from_env(name, pcfg, cfg["transfer"], clock)
    return out
