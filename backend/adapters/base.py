"""The EnvironmentAdapter contract (BUILD_PLAN.md 5.9).

The agent sees the world only through these six typed methods. This demo implements them with
the simulator; a production deployment implements them with observability and ops clients, and
the agent core, memory loop and learning stay unchanged.

Deliberately absent: triggering incidents, advancing time, resetting — those are the
simulator's control plane, and a real factory has no such API. Also absent: any ground truth.
`execute_action` returns a receipt, not an effect; the agent learns whether a fix worked only by
observing recovery.
"""

from typing import Protocol, runtime_checkable

from backend.schemas import (
    Action,
    ActionReceipt,
    Event,
    MachineMetrics,
    Metric,
    MetricPoint,
    RecoveryObservation,
)


class AdapterError(Exception):
    """A rejected request (unknown machine, closed incident, bad argument). Never a crash."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@runtime_checkable
class EnvironmentAdapter(Protocol):
    # Read side — backs the four LLM tools (BUILD_PLAN.md 7.3).
    def get_machine_metrics(self, machine_id: str) -> MachineMetrics: ...

    def get_metric_history(
        self, machine_id: str, metric: Metric, window_hours: float
    ) -> list[MetricPoint]: ...

    def get_recent_events(self, machine_id: str, window_minutes: float) -> list[Event]: ...

    def get_error_logs(self, machine_id: str, window_minutes: float) -> list[str]: ...

    # Write side — used only by the act node, after human approval.
    def execute_action(self, incident_id: str, action: Action) -> ActionReceipt: ...

    # Used by the verify node.
    def observe_recovery(self, incident_id: str, window_sim_s: int) -> RecoveryObservation: ...
