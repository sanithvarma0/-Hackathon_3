from collections.abc import Callable

import pytest

from backend.adapters import SimulatorAdapter
from backend.simulator import ManualClock, SimNotification, Simulator
from backend.simulator.db import connect

START = 1_790_000_000.0


class World:
    """A simulator on a manual clock, plus helpers to move time and collect notifications."""

    def __init__(self, seed: int = 7) -> None:
        self.clock = ManualClock(START)
        self.conn = connect(":memory:")
        self.sim = Simulator(self.conn, self.clock, seed=seed)
        self.adapter = SimulatorAdapter(self.sim)
        self.notes: list[SimNotification] = []
        self.sim.subscribe(self.notes.append)

    def advance(self, seconds: float) -> None:
        self.clock.advance(seconds)
        self.sim.tick()

    def advance_until(self, predicate: Callable[[], bool], limit_s: int = 3600) -> int:
        waited = 0
        while not predicate():
            if waited >= limit_s:
                raise AssertionError("condition not reached")
            self.advance(10)
            waited += 10
        return waited


@pytest.fixture
def world() -> World:
    return World()
