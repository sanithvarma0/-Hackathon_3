"""Environment adapters: the agent's only view of the world (BUILD_PLAN.md 5.9)."""

from backend.adapters.base import AdapterError, EnvironmentAdapter, IncidentLifecycle
from backend.adapters.simulator import SimulatorAdapter, SimulatorLifecycle

__all__ = [
    "AdapterError",
    "EnvironmentAdapter",
    "IncidentLifecycle",
    "SimulatorAdapter",
    "SimulatorLifecycle",
]
