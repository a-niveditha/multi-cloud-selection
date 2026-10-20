"""Shared data types: the interfaces between the four modules.

Who produces / consumes what:
  network layer   -> ProbeResult                      -> selection engine, DB
  selection       -> Decision                          -> transfer layer, DB
  transfer layer  -> AttemptResult (one per provider)  -> risk history, DB
                  -> UploadResult  (whole transfer)    -> DB, analysis
  runner          -> RunContext (stamped on every DB row)

All times are in seconds and are measured with a Clock (see clock.py), so the
mock providers can use simulated time in tests.
"""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Strategy(str, Enum):
    PROPOSED = "proposed"              # risk-aware adaptive
    STATIC_NETWORK = "static_network"  # live network metrics only
    COST_ONLY = "cost_only"            # cheapest by published pricing
    NEAREST = "nearest"                # fixed-nearest-region, chosen once at setup
    RANDOM = "random"                  # uniform random, seeded


class Condition(str, Enum):
    NATURAL = "natural"
    MILD = "mild"
    SEVERE = "severe"


@dataclass(frozen=True)
class ProbeResult:
    """Network measurement of one provider just before a decision."""
    provider: str
    rtt_ms: float | None            # None if the probe failed
    throughput_mbps: float | None
    loss_pct: float | None          # 0-100
    probe_time_s: float             # time spent probing this provider
    ok: bool = True
    error: str | None = None


@dataclass(frozen=True)
class Decision:
    """Output of a strategy: providers ranked best first, plus why."""
    strategy: Strategy
    ranking: tuple[str, ...]                                    # best first; failover follows this order
    scores: dict[str, float] = field(default_factory=dict)      # empty for cost_only / nearest / random
    weights: dict[str, float] = field(default_factory=dict)     # {"network", "cost", "risk"}
    risk: dict[str, float] = field(default_factory=dict)        # rho_i per provider
    profile: str | None = None                                  # e.g. "normal" / "severe" (proposed only)

    @property
    def chosen(self) -> str:
        return self.ranking[0]


@dataclass(frozen=True)
class AttemptResult:
    """One upload attempt to one provider (one row in `attempts`)."""
    provider: str
    object_key: str
    transfer_time_s: float          # upload duration; for a timeout this is the time-to-failure
    success: bool                   # upload call completed without error
    timed_out: bool
    verified: bool | None           # checksum matched; None if upload failed (not checked)
    verify_time_s: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Upload succeeded AND was verified."""
        return self.success and bool(self.verified)


@dataclass(frozen=True)
class UploadResult:
    """The whole transfer for one strategy in one round, after any failover."""
    provider: str                   # provider of the final attempt
    first_choice: str
    probe_time_s: float             # 0 for strategies that do not probe
    transfer_time_s: float          # duration of the final attempt
    end_to_end_time_s: float        # probe + all attempts + failover gaps (excludes final verification)
    success: bool                   # final attempt uploaded and verified
    timed_out: bool                 # any attempt timed out
    retry_time_s: float | None      # start of first failed attempt -> start of next attempt; None if no retry
    verified: bool
    attempts: tuple[AttemptResult, ...] = ()

    @property
    def n_failed_attempts(self) -> int:
        return sum(1 for a in self.attempts if not a.ok)


@dataclass(frozen=True)
class RunContext:
    """Stamped on every DB row."""
    machine_id: str
    code_version: str               # git commit (plus "-dirty" if overridden)
    block_id: str
    config_hash: str
    is_pilot: bool = False


@dataclass(frozen=True)
class LocalFile:
    """The payload file and its checksums (computed once, compared after upload)."""
    path: str
    size_bytes: int
    md5_hex: str                    # compared by MockProvider.verify
    md5_b64: str
    sha1_hex: str

    @classmethod
    def from_path(cls, path: str | Path) -> "LocalFile":
        md5, sha1 = hashlib.md5(), hashlib.sha1()
        size = 0
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                md5.update(chunk)
                sha1.update(chunk)
                size += len(chunk)
        return cls(
            path=str(path),
            size_bytes=size,
            md5_hex=md5.hexdigest(),
            md5_b64=base64.b64encode(md5.digest()).decode("ascii"),
            sha1_hex=sha1.hexdigest(),
        )
