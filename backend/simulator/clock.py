"""Simulated clock (BUILD_PLAN.md 5.1).

All simulator dynamics and MTTR run on sim time. `RealtimeClock` runs `speed` sim-seconds per
real second for the live demo; `ManualClock` only moves when told to, for tests and the eval
harness's fast-forward mode.
"""

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Current sim time in seconds since the Unix epoch."""
        ...


class RealtimeClock:
    def __init__(self, speed: float, start: float | None = None) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        self.speed = speed
        self._start_sim = time.time() if start is None else start
        self._start_real = time.monotonic()

    def now(self) -> float:
        return self._start_sim + (time.monotonic() - self._start_real) * self.speed


class ManualClock:
    def __init__(self, start: float) -> None:
        self._now = start

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("cannot move time backwards")
        self._now += seconds
