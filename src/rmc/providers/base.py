"""Common interface for storage providers (S3, GCS, B2, mock).

An adapter implements only the SDK-specific methods:
  put_object(file, key, timeout_s, overwrite)
                                    upload; raise TimeoutError on timeout, any other exception on failure.
                                    Trial uploads use overwrite=False (unique keys); only the
                                    throughput probe reuses its key and passes overwrite=True.
  verify(file, key) -> bool         compare the provider's stored checksum/ETag with the local one,
                                    WITHOUT downloading the object
  delete_object(key)                remove an uploaded object (cleanup, never timed)

`upload()` (shared) does the timing, timeout/failure classification and verification,
and returns an AttemptResult. Failover across providers is NOT done here; it is in
rmc.transfer, which calls upload() on the next-ranked provider.

Timeouts: real adapters set `enforce_deadline = True`, so put_object runs in a
worker thread and upload() stops waiting after timeout_s (a hard wall-clock
deadline, whatever the SDK does). LIMITATION: Python cannot kill a thread, so an
abandoned upload may keep running in the background until the SDK's own socket
timeout ends it, and can share bandwidth with the failover attempt. The adapters
set SDK socket timeouts and disable SDK retries to keep this short.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod

from rmc.clock import RealClock
from rmc.models import AttemptResult, LocalFile


class StorageProvider(ABC):
    enforce_deadline = False   # real adapters set True; the mock enforces timeouts itself

    def __init__(self, name: str, clock=None):
        self.name = name
        self.clock = clock or RealClock()

    @abstractmethod
    def put_object(self, file: LocalFile, key: str, timeout_s: float, overwrite: bool = False) -> None: ...

    @abstractmethod
    def verify(self, file: LocalFile, key: str) -> bool: ...

    def delete_object(self, key: str) -> None:
        raise NotImplementedError

    def put_with_deadline(self, file: LocalFile, key: str, timeout_s: float, overwrite: bool = False) -> None:
        """put_object, but raise TimeoutError after timeout_s even if the SDK hangs."""
        if not self.enforce_deadline:
            self.put_object(file, key, timeout_s, overwrite)
            return
        outcome: dict = {}

        def work():
            try:
                self.put_object(file, key, timeout_s, overwrite)
                outcome["ok"] = True
            except BaseException as e:   # passed back to the caller below
                outcome["error"] = e

        t = threading.Thread(target=work, name=f"upload-{self.name}", daemon=True)
        t.start()
        t.join(timeout_s)
        if t.is_alive():
            raise TimeoutError(f"no completion within {timeout_s}s (upload thread abandoned)")
        if "error" in outcome:
            raise outcome["error"]

    def upload(self, file: LocalFile, key: str, timeout_s: float) -> AttemptResult:
        start = self.clock.now()
        try:
            self.put_with_deadline(file, key, timeout_s)
        except TimeoutError as e:
            return self._failed(key, start, timed_out=True, error=f"timeout: {e}")
        except Exception as e:  # any SDK / network error counts as a failed attempt
            return self._failed(key, start, timed_out=False, error=f"{type(e).__name__}: {e}")

        transfer_time = self.clock.now() - start
        if transfer_time > timeout_s:
            return self._failed(key, start, timed_out=True,
                                error=f"completed after timeout ({transfer_time:.2f}s > {timeout_s}s)")

        verify_start = self.clock.now()
        try:
            verified, error = self.verify(file, key), None
        except Exception as e:
            verified, error = False, f"verify {type(e).__name__}: {e}"
        if not verified and error is None:
            error = "checksum mismatch"
        return AttemptResult(
            provider=self.name, object_key=key, transfer_time_s=transfer_time, success=True,
            timed_out=False, verified=verified, verify_time_s=self.clock.now() - verify_start, error=error,
        )

    def _failed(self, key: str, start: float, timed_out: bool, error: str) -> AttemptResult:
        return AttemptResult(
            provider=self.name, object_key=key, transfer_time_s=self.clock.now() - start,
            success=False, timed_out=timed_out, verified=None, error=error,
        )
