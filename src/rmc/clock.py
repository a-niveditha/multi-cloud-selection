"""Clocks. Real code uses RealClock; tests and mock runs use SimClock so that
simulated transfers take "time" without actually sleeping."""
from __future__ import annotations

import time


class RealClock:
    def now(self) -> float:
        return time.perf_counter()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class SimClock:
    def __init__(self, start: float = 0.0):
        self._t = start

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._t += seconds
