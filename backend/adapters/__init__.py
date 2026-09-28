"""Environment adapters: the agent's only view of the world (BUILD_PLAN.md 5.9)."""

from backend.adapters.base import AdapterError, EnvironmentAdapter
from backend.adapters.simulator import SimulatorAdapter

__all__ = ["AdapterError", "EnvironmentAdapter", "SimulatorAdapter"]
