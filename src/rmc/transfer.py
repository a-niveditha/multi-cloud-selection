"""Transfer layer: upload to the chosen provider, fail over down the ranking.

    result = transfer_with_failover(decision, providers, file, key_base, cfg["transfer"],
                                    probe_time_s=..., clock=...)

Per attempt, in decision.ranking order (at most transfer.max_attempts):
  upload + verify (provider.upload). If it timed out, failed, or (when
  retry_on_verify_failure is true) failed verification, move to the next provider.

Timing definitions (all from the same clock):
  transfer_time_s    duration of the FINAL attempt's upload
  end_to_end_time_s  probe_time_s + (start of first upload -> end of final upload).
                     Includes time lost on failed attempts and on verifying a failed
                     attempt (that is how the failure was detected), but NOT the final
                     verification.
  retry_time_s       failure-to-retry: start of the first failed attempt -> start of
                     the next attempt. None if there was no retry.

This module only uploads. Updating the risk history from result.attempts is the
caller's job (the runner), so that history_source / history_scope stay in one place.
"""
from __future__ import annotations

from rmc.clock import RealClock
from rmc.models import AttemptResult, Decision, LocalFile, UploadResult
from rmc.providers.base import StorageProvider


def attempt_failed(a: AttemptResult, retry_on_verify_failure: bool) -> bool:
    if not a.success:
        return True
    return retry_on_verify_failure and not a.verified


def transfer_with_failover(decision: Decision, providers: dict[str, StorageProvider], file: LocalFile,
                           key_base: str, tcfg: dict, probe_time_s: float = 0.0, clock=None) -> UploadResult:
    """key_base must be unique per transfer, e.g. 'rmc-trials/B03/R5/run1/proposed'.
    Each attempt uploads to '<key_base>/<attempt_no>-<provider>.bin'."""
    clock = clock or RealClock()
    attempts: list[AttemptResult] = []
    first_fail_start: float | None = None
    retry_time: float | None = None
    t0 = clock.now()
    last_start = last_end = t0

    for attempt_no, name in enumerate(decision.ranking[: tcfg["max_attempts"]], start=1):
        last_start = clock.now()
        if first_fail_start is not None and retry_time is None:
            retry_time = last_start - first_fail_start
        a = providers[name].upload(file, f"{key_base}/{attempt_no}-{name}.bin", tcfg["timeout_s"])
        attempts.append(a)
        last_end = last_start + a.transfer_time_s
        if not attempt_failed(a, tcfg["retry_on_verify_failure"]):
            break
        if first_fail_start is None:
            first_fail_start = last_start

    final = attempts[-1]
    return UploadResult(
        provider=final.provider,
        first_choice=decision.chosen,
        probe_time_s=probe_time_s,
        transfer_time_s=final.transfer_time_s,
        end_to_end_time_s=probe_time_s + (last_end - t0),
        success=final.ok,
        timed_out=any(a.timed_out for a in attempts),
        retry_time_s=retry_time,
        verified=bool(final.verified),
        attempts=tuple(attempts),
    )
