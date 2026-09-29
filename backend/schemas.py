"""Typed contracts shared by the simulator, the environment adapter, the agent and the API."""

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

Action = Literal[
    "ROLLBACK_CONFIG",
    "RESTART_MACHINE",
    "RECALIBRATE_SENSOR",
    "RESTART_GATEWAY",
    "CLEAR_CACHE",
    "ESCALATE_HUMAN",
]
ACTIONS: tuple[Action, ...] = get_args(Action)

IncidentType = Literal[
    "config_regression",
    "sensor_drift",
    "network_failure",
    "resource_exhaustion",
    "ambiguous",
    "vision_link_dropout",
    "servo_tuning_drift",
]
PREDEFINED_TYPES: tuple[IncidentType, ...] = (
    "config_regression",
    "sensor_drift",
    "network_failure",
    "resource_exhaustion",
)
# Site knowledge (BUILD_PLAN 5.4b): the fix cannot be derived from the signals — it is known
# only to the plant's engineers, so the first occurrence should escalate and memory is what
# lets the agent fix the next one itself.
SITE_KNOWLEDGE_TYPES: tuple[IncidentType, ...] = ("vision_link_dropout", "servo_tuning_drift")

MachineStatus = Literal["healthy", "degraded", "critical", "recovering"]
IncidentStatus = Literal["open", "awaiting_action", "verifying", "resolved", "escalated"]
Effect = Literal["full_recovery", "partial_recovery", "no_effect", "escalated"]

Metric = Literal[
    "throughput_pct",
    "oee_pct",
    "error_rate_pct",
    "temperature_c",
    "sensor_variance",
    "packet_loss_pct",
    "latency_ms",
    "memory_pct",
]
METRICS: tuple[Metric, ...] = get_args(Metric)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ---- Agent-visible world (returned through the EnvironmentAdapter) ----------------------------


class MachineMetrics(_Frozen):
    machine_id: str
    name: str
    profile: str
    gateway: str
    status: MachineStatus
    throughput_pct: float
    oee_pct: float
    error_rate_pct: float
    temperature_c: float
    sensor_variance: float
    packet_loss_pct: float
    latency_ms: float
    memory_pct: float
    config_version: str
    calibration_age_days: float
    ts: int


class MetricPoint(_Frozen):
    ts: int
    value: float


class Event(_Frozen):
    ts: int
    machine_id: str
    event_type: str
    detail: str


class ActionReceipt(_Frozen):
    """What an ops API returns when an action is executed: confirmation only.

    Deliberately carries no effect. The agent must observe whether the fix held (verify);
    the true effect is ground truth kept inside the simulator for evaluation.
    """

    incident_id: str
    action: Action
    executed_ts: int
    message: str


class RecoveryObservation(_Frozen):
    incident_id: str
    window_sim_s: int
    elapsed_sim_s: int
    complete: bool  # the full window has elapsed
    recovered: bool  # affected machines climbed back to healthy after the action
    held: bool  # ...and stayed healthy for the whole observed window
    peak_throughput_pct: float
    min_throughput_pct: float
    current_throughput_pct: float


# ---- Control plane (simulator only: API, eval harness, dashboard) -----------------------------


class CustomIncidentSpec(_Frozen):
    """Structured custom-builder input (BUILD_PLAN.md 5.7). No free text anywhere."""

    machine: str
    config_changed: bool = False
    minutes_before: int = Field(10, ge=1, le=60)
    throughput_delta: int = Field(-30, ge=-50, le=-5)
    error_rate: float = Field(4.0, ge=0, le=20)
    temperature: Literal["normal", "high"] = "normal"
    calibration: Literal["fresh", "old"] = "fresh"
    network: Literal["normal", "degraded"] = "normal"
    memory_trend: Literal["flat", "climbing"] = "flat"


class Incident(_Frozen):
    id: str
    type: IncidentType  # ground truth — never shown to the agent
    machine_id: str
    affected: tuple[str, ...]
    gateway: str | None
    status: IncidentStatus
    onset_ts: int
    detected_ts: int | None
    resolved_ts: int | None
    human_wait_sim_s: int
    resolution_action: Action | None
    mttr_sim_s: int | None
    engineer_action: Action | None = None
    engineer_note: str | None = None


class ActionRecord(_Frozen):
    """Ground-truth record of an executed action (eval + Memory Browser, never the agent)."""

    ts: int
    incident_id: str
    attempt: int
    action: Action
    effect: Effect
    recovery_pct: float | None
    re_degraded_after_sim_s: int | None
    executed_by: str


class Alert(_Frozen):
    """What monitoring tells the agent when an incident is detected. No diagnosis."""

    incident_id: str
    machine_id: str
    machine_name: str
    machine_profile: str
    detected_ts: int
    throughput_pct: float
    nominal_throughput_pct: float
    alerting_machines: tuple[str, ...]  # every machine currently below its alert threshold


class Resolution(_Frozen):
    incident_id: str
    status: Literal["resolved", "escalated"]
    mttr_sim_s: int
    human_wait_sim_s: int
    # After an escalation the on-call engineer's resolution note is on the ticket — real,
    # after-the-fact knowledge the agent can learn from (None: fixed outside the action set).
    engineer_action: Action | None = None
    engineer_note: str | None = None


class GatewayState(_Frozen):
    gateway_id: str
    machines: tuple[str, ...]
    status: MachineStatus
    packet_loss_pct: float


class PlantState(_Frozen):
    ts: int
    machines: tuple[MachineMetrics, ...]
    starved: tuple[str, ...]  # machines whose inbound flow is limited upstream (line-level)
    gateways: tuple[GatewayState, ...]
    line_throughput_pct: float
    oee_pct: float
    alerts: int
    active_incident_id: str | None
