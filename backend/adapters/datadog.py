"""Production adapter sketch: how each method maps to real infrastructure. Not implemented.

This file exists to make the boundary concrete. A real deployment implements
`EnvironmentAdapter` against its own stack; the agent, memory loop and learning are unchanged.

| Method               | Production source                                                  |
|----------------------|--------------------------------------------------------------------|
| get_machine_metrics  | Datadog metrics query (latest point per metric, tagged by asset)   |
| get_metric_history   | Datadog metrics query over a time window, rolled up to <= 60 points |
| get_recent_events    | Datadog events / change-management feed (deploys, calibrations)    |
| get_error_logs       | Datadog log search, WARN and above, for the asset's controller     |
| execute_action       | Runbook automation (e.g. Rundeck / PagerDuty Automation Actions)   |
| observe_recovery     | Metrics query after the action, compared with the asset's baseline |
"""

from backend.schemas import (
    Action,
    ActionReceipt,
    Event,
    MachineMetrics,
    Metric,
    MetricPoint,
    RecoveryObservation,
)

_MESSAGE = "DatadogAdapter is a documented sketch; the demo uses SimulatorAdapter"


class DatadogAdapter:
    def get_machine_metrics(self, machine_id: str) -> MachineMetrics:
        raise NotImplementedError(_MESSAGE)

    def get_metric_history(
        self, machine_id: str, metric: Metric, window_hours: float
    ) -> list[MetricPoint]:
        raise NotImplementedError(_MESSAGE)

    def get_recent_events(self, machine_id: str, window_minutes: float) -> list[Event]:
        raise NotImplementedError(_MESSAGE)

    def get_error_logs(self, machine_id: str, window_minutes: float) -> list[str]:
        raise NotImplementedError(_MESSAGE)

    def execute_action(self, incident_id: str, action: Action) -> ActionReceipt:
        raise NotImplementedError(_MESSAGE)

    def observe_recovery(self, incident_id: str, window_sim_s: int) -> RecoveryObservation:
        raise NotImplementedError(_MESSAGE)
