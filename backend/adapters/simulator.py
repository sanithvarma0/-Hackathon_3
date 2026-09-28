"""EnvironmentAdapter backed by the deterministic simulator."""

from collections.abc import Callable
from typing import TypeVar

from backend.adapters.base import AdapterError
from backend.schemas import (
    Action,
    ActionReceipt,
    Alert,
    Event,
    MachineMetrics,
    Metric,
    MetricPoint,
    RecoveryObservation,
    Resolution,
)
from backend.simulator import Simulator, SimulatorError

T = TypeVar("T")

MAX_WINDOW_HOURS = 24 * 7
MAX_WINDOW_MINUTES = 24 * 60


def _translate(call: Callable[[], T]) -> T:
    try:
        return call()
    except SimulatorError as e:
        raise AdapterError(e.code, e.message) from e


def _require_window(value: float, maximum: float, unit: str) -> None:
    if not 0 < value <= maximum:
        raise AdapterError("INVALID_WINDOW", f"window must be in (0, {maximum}] {unit}")


class SimulatorAdapter:
    def __init__(self, simulator: Simulator, *, executed_by: str = "agent") -> None:
        self._sim = simulator
        self._executed_by = executed_by

    def get_machine_metrics(self, machine_id: str) -> MachineMetrics:
        return _translate(lambda: self._sim.get_machine_metrics(machine_id))

    def get_metric_history(
        self, machine_id: str, metric: Metric, window_hours: float
    ) -> list[MetricPoint]:
        _require_window(window_hours, MAX_WINDOW_HOURS, "hours")
        return _translate(lambda: self._sim.get_metric_history(machine_id, metric, window_hours))

    def get_recent_events(self, machine_id: str, window_minutes: float) -> list[Event]:
        _require_window(window_minutes, MAX_WINDOW_MINUTES, "minutes")
        return _translate(lambda: self._sim.get_recent_events(machine_id, window_minutes))

    def get_error_logs(self, machine_id: str, window_minutes: float) -> list[str]:
        _require_window(window_minutes, MAX_WINDOW_MINUTES, "minutes")
        return _translate(lambda: self._sim.get_error_logs(machine_id, window_minutes))

    def execute_action(self, incident_id: str, action: Action) -> ActionReceipt:
        return _translate(
            lambda: self._sim.execute_action(incident_id, action, executed_by=self._executed_by)
        )

    def observe_recovery(self, incident_id: str, window_sim_s: int) -> RecoveryObservation:
        if window_sim_s <= 0:
            raise AdapterError("INVALID_WINDOW", "window_sim_s must be positive")
        return _translate(lambda: self._sim.observe_recovery(incident_id, window_sim_s))


class SimulatorLifecycle:
    def __init__(self, simulator: Simulator) -> None:
        self._sim = simulator

    def get_alert(self, incident_id: str) -> Alert:
        return _translate(lambda: self._sim.alert(incident_id))

    def acknowledge(self, incident_id: str) -> None:
        _translate(lambda: self._sim.begin_wait(incident_id))

    def resolve(self, incident_id: str) -> Resolution:
        def close() -> Resolution:
            incident = self._sim.get_incident(incident_id)
            if incident.status not in ("resolved", "escalated"):
                incident = self._sim.close_incident(incident_id)
            assert incident.mttr_sim_s is not None
            return Resolution(
                incident_id=incident_id,
                status="escalated" if incident.status == "escalated" else "resolved",
                mttr_sim_s=incident.mttr_sim_s,
                human_wait_sim_s=incident.human_wait_sim_s,
            )

        return _translate(close)
