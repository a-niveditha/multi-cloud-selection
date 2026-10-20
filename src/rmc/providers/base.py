"""Common interface for storage providers (implemented by the simulated MockProvider).

A provider implements:
  put_object(file, key, timeout_s, overwrite)
                                    upload; raise TimeoutError on timeout, any other exception on failure.
                                    Trial uploads use overwrite=False (unique keys).
  verify(file, key) -> bool         compare the stored checksum with the local one
  delete_object(key)                remove an uploaded object (cleanup, never timed)

`upload()` (shared) does the timing, timeout/failure classification and verification,
and returns an AttemptResult. Failover across providers is NOT done here; it is in
rmc.transfer, which calls upload() on the next-ranked provider.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from rmc.clock import RealClock
from rmc.models import AttemptResult, LocalFile


class StorageProvider(ABC):
    def __init__(self, name: str, clock=None):
        self.name = name
        self.clock = clock or RealClock()

    @abstractmethod
    def put_object(self, file: LocalFile, key: str, timeout_s: float, overwrite: bool = False) -> None: ...

    @abstractmethod
    def verify(self, file: LocalFile, key: str) -> bool: ...

    def delete_object(self, key: str) -> None:
        raise NotImplementedError

    def upload(self, file: LocalFile, key: str, timeout_s: float) -> AttemptResult:
        start = self.clock.now()
        try:
            self.put_object(file, key, timeout_s)
        except TimeoutError as e:
            return self._failed(key, start, timed_out=True, error=f"timeout: {e}")
        except Exception as e:  # any upload error counts as a failed attempt
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
