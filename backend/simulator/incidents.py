"""Incident taxonomy, effects matrix and incident specs (BUILD_PLAN.md 5.4-5.7).

An incident is a *stable signature* (the signals that identify its class) plus a *noisy
surface* (machine, magnitude, timing, log wording). Signals are driven by `SignalFlags`, so
predefined and custom incidents are rendered by the same code; the class only decides which
fixes work.
"""

import random
from dataclasses import dataclass, field

from backend.schemas import Action, CustomIncidentSpec, Effect, IncidentType
from backend.simulator.machines import MACHINES

CORRECT_FIX: dict[IncidentType, Action] = {
    "config_regression": "ROLLBACK_CONFIG",
    "sensor_drift": "RECALIBRATE_SENSOR",
    "network_failure": "RESTART_GATEWAY",
    "resource_exhaustion": "CLEAR_CACHE",
    "ambiguous": "ESCALATE_HUMAN",
    # site knowledge: not derivable from the signals (see SITE_KNOWLEDGE_TYPES)
    "vision_link_dropout": "RESTART_GATEWAY",  # camera PoE port on the gateway switch
    "servo_tuning_drift": "RESTART_MACHINE",  # tuning table reloads only on a cold restart
}


@dataclass(frozen=True)
class EffectSpec:
    effect: Effect
    recovery_pct: tuple[float, float] | None = None  # partial: throughput it recovers to
    re_degrade_after_sim_s: tuple[int, int] | None = None  # partial: when relief wears off


FULL = EffectSpec("full_recovery")
NONE = EffectSpec("no_effect")

# Complete matrix (5.5). Anything not listed has no effect; ESCALATE_HUMAN is handled by the
# engine for every class. Temporary relief recovers to 90-94% so the machine *looks* fixed —
# only watching it through the verify window reveals the trap.
EFFECTS: dict[IncidentType, dict[Action, EffectSpec]] = {
    "config_regression": {
        "ROLLBACK_CONFIG": FULL,
        "RESTART_MACHINE": EffectSpec("partial_recovery", (90.0, 94.0), (60, 120)),
    },
    "sensor_drift": {"RECALIBRATE_SENSOR": FULL},
    "network_failure": {"RESTART_GATEWAY": FULL},
    "resource_exhaustion": {
        "CLEAR_CACHE": FULL,
        "RESTART_MACHINE": EffectSpec("partial_recovery", (90.0, 95.0), (120, 150)),
    },
    "ambiguous": {},
    "vision_link_dropout": {"RESTART_GATEWAY": FULL},
    "servo_tuning_drift": {"RESTART_MACHINE": FULL},
}


def effect_of(incident_type: IncidentType, action: Action) -> EffectSpec:
    return EFFECTS[incident_type].get(action, NONE)


@dataclass(frozen=True)
class SignalFlags:
    """Which signals the incident produces — what the agent can discover with its tools."""

    onset: str  # "gradual" | "sudden"
    config_changed_min_before: int | None = None
    calibration_age_days: float | None = None  # None = fresh (machine's normal schedule)
    phantom_temperature: bool = False
    high_sensor_variance: bool = False
    network_degraded: bool = False
    memory_climbing: bool = False
    vision_dropout: bool = False  # vision timeouts + slightly elevated loss on one machine
    servo_drift: bool = False  # following errors, stale tuning table after the nightly check


@dataclass(frozen=True)
class IncidentSpec:
    type: IncidentType
    machine_id: str
    depth: float  # fraction of throughput lost at full degradation
    ramp_sim_s: int  # onset duration (1 for sudden)
    flags: SignalFlags
    error_extra_pct: float
    packet_loss_pct: float = 0.0
    latency_ms: float = 0.0
    phantom_delta_c: float = 0.0
    variance_high: float = 0.0
    params: dict[str, float] = field(default_factory=dict)


# Surface noise: log wording. >= 5 variants per class so repeats never share phrasing.
LOG_TEMPLATES: dict[IncidentType, tuple[tuple[str, str], ...]] = {
    "config_regression": (
        ("ERROR", "servo timeout on axis {axis} (limit {lim} ms)"),
        ("WARN", "cycle time +{pct}% vs nominal ({nominal}s -> {actual}s)"),
        ("WARN", "control loop jitter, position error {err} mm"),
        ("WARN", "feed override clamped at {clamp}% by controller"),
        ("ERROR", "spindle load oscillation +/-{pct}% after parameter reload"),
        ("WARN", "motion planner lookahead underrun ({n} blocks)"),
    ),
    "sensor_drift": (
        ("ALARM", "temperature alarm {t}C on {sensor} (IR probe reads {ir}C)"),
        ("WARN", "probe repeatability out of tolerance: sigma={sigma} mm"),
        ("WARN", "thermal compensation offset {off} um exceeds expected band"),
        ("WARN", "vision gauge reject rate {pct}% (false positives suspected)"),
        ("ALARM", "{sensor} reads {t}C, loop sensor reads {ir}C"),
    ),
    "network_failure": (
        ("ERROR", "OPC UA session to {gw} timed out after {ms} ms"),
        ("ERROR", "MES heartbeat missed ({n} consecutive)"),
        ("WARN", "PROFINET frame loss {loss}% on port X{port}"),
        ("WARN", "PLC <-> SCADA round-trip {ms} ms (threshold 50 ms)"),
        ("ERROR", "work order download retry {n}/5 failed"),
    ),
    "resource_exhaustion": (
        ("WARN", "controller memory {mem}% (hmi-cache {mb} MB)"),
        ("ERROR", "OOM killer terminated {proc} (pid {pid})"),
        ("WARN", "HMI frame render {ms} ms, UI lag reported by operator"),
        ("WARN", "tool-path cache eviction storm ({n} evictions/min)"),
        ("WARN", "swap usage {swap}% on edge controller"),
    ),
    "ambiguous": (
        ("WARN", "cycle time +{pct}% vs nominal ({nominal}s -> {actual}s)"),
        ("WARN", "part quality drift flagged by SPC on station"),
        ("WARN", "operator note: intermittent slowdowns, cause unknown"),
    ),
    "vision_link_dropout": (
        ("WARN", "vision frame timeout on cam-{n} ({ms} ms)"),
        ("WARN", "part presence check no-read, retrying"),
        ("ERROR", "gripper held: waiting for vision confirmation ({s}s)"),
        ("WARN", "vision worker reconnecting to camera stream"),
        ("WARN", "image acquisition dropped {n} frames"),
    ),
    "servo_tuning_drift": (
        ("WARN", "axis {axis} following error {err} mm (warning band)"),
        ("WARN", "cycle time +{pct}% vs nominal ({nominal}s -> {actual}s)"),
        ("WARN", "servo tuning table checksum mismatch (cached copy in use)"),
        ("WARN", "axis {axis} settling time exceeded by {ms} ms"),
        ("INFO", "adaptive feed reduced to {clamp}% to hold tolerance"),
    ),
}

NOISE_LOGS: tuple[tuple[str, str], ...] = (
    ("INFO", "shift handover checklist completed"),
    ("INFO", "tool change T{n} completed in {s}s"),
    ("INFO", "coolant concentration {c}% within spec"),
    ("INFO", "part counter {count}"),
    ("WARN", "door interlock opened by operator"),
    ("WARN", "preventive maintenance due in {h} h: lubrication"),
    ("WARN", "air pressure {bar} bar, low band"),
)

SENSORS = ("spindle bearing sensor", "coolant temp sensor", "servo amp thermistor")
PROCESSES = ("hmi-cache", "toolpath-svc", "opcua-bridge", "vision-worker")


def render_log(template: str, machine_id: str, rng: random.Random) -> str:
    m = MACHINES[machine_id]
    nominal = rng.uniform(38, 95)
    pct = rng.randint(12, 45)
    values: dict[str, object] = {
        "axis": rng.choice(m.axes),
        "lim": rng.choice((8, 12, 16)),
        "pct": pct,
        "nominal": f"{nominal:.1f}",
        "actual": f"{nominal * (1 + pct / 100):.1f}",
        "err": f"{rng.uniform(0.04, 0.25):.2f}",
        "clamp": rng.choice((60, 65, 70, 75)),
        "n": rng.randint(2, 9),
        "t": rng.randint(74, 92),
        "ir": rng.randint(38, 47),
        "sensor": rng.choice(SENSORS),
        "sigma": f"{rng.uniform(0.012, 0.04):.3f}",
        "off": rng.randint(18, 60),
        "gw": m.gateway,
        "ms": rng.randint(120, 900),
        "loss": rng.randint(8, 15),
        "port": rng.randint(1, 4),
        "mem": rng.randint(88, 97),
        "mb": rng.randint(1800, 3900),
        "proc": rng.choice(PROCESSES),
        "pid": rng.randint(1200, 9800),
        "swap": rng.randint(35, 80),
        "s": rng.randint(4, 12),
        "c": f"{rng.uniform(6.5, 8.5):.1f}",
        "count": rng.randint(1000, 60000),
        "h": rng.randint(2, 40),
        "bar": f"{rng.uniform(5.4, 5.9):.1f}",
    }
    return template.format(**values)


def generate_spec(incident_type: IncidentType, machine_id: str, rng: random.Random) -> IncidentSpec:
    """Sample a predefined incident: stable signature, noisy surface."""
    if incident_type == "config_regression":
        return IncidentSpec(
            type=incident_type,
            machine_id=machine_id,
            depth=rng.uniform(0.25, 0.45),
            ramp_sim_s=rng.randint(300, 600),
            flags=SignalFlags(onset="gradual", config_changed_min_before=rng.randint(5, 20)),
            error_extra_pct=rng.uniform(3, 8),
        )
    if incident_type == "sensor_drift":
        return IncidentSpec(
            type=incident_type,
            machine_id=machine_id,
            depth=rng.uniform(0.15, 0.30),
            ramp_sim_s=rng.randint(300, 600),
            flags=SignalFlags(
                onset="gradual",
                calibration_age_days=rng.uniform(31, 60),
                phantom_temperature=True,
                high_sensor_variance=True,
            ),
            error_extra_pct=rng.uniform(2, 5),
            phantom_delta_c=rng.uniform(28, 45),
            variance_high=rng.uniform(0.2, 0.4),
        )
    if incident_type == "network_failure":
        return IncidentSpec(
            type=incident_type,
            machine_id=machine_id,
            depth=rng.uniform(0.40, 0.65),
            ramp_sim_s=1,
            flags=SignalFlags(onset="sudden", network_degraded=True),
            error_extra_pct=rng.uniform(4, 10),
            packet_loss_pct=rng.uniform(8, 15),
            latency_ms=rng.uniform(80, 200),
        )
    if incident_type == "resource_exhaustion":
        return IncidentSpec(
            type=incident_type,
            machine_id=machine_id,
            depth=rng.uniform(0.20, 0.30),
            ramp_sim_s=rng.randint(300, 600),
            flags=SignalFlags(onset="gradual", memory_climbing=True),
            error_extra_pct=rng.uniform(2, 6),
        )
    if incident_type == "vision_link_dropout":
        return IncidentSpec(
            type=incident_type,
            machine_id=machine_id,
            depth=rng.uniform(0.16, 0.28),
            ramp_sim_s=rng.randint(300, 900),
            flags=SignalFlags(onset="gradual", vision_dropout=True),
            error_extra_pct=rng.uniform(1, 3),
            packet_loss_pct=rng.uniform(0.5, 1.0),  # barely above normal, this machine only
            latency_ms=rng.uniform(3, 8),
        )
    if incident_type == "servo_tuning_drift":
        return IncidentSpec(
            type=incident_type,
            machine_id=machine_id,
            depth=rng.uniform(0.16, 0.28),
            ramp_sim_s=rng.randint(600, 1200),
            flags=SignalFlags(onset="gradual", servo_drift=True),
            error_extra_pct=rng.uniform(1.5, 4),
        )
    raise ValueError(f"not a generated incident type: {incident_type}")


def classify_custom(spec: CustomIncidentSpec) -> IncidentType:
    """Strongest signal wins (5.7). The agent never sees this result."""
    if spec.network == "degraded":
        return "network_failure"
    if spec.config_changed and 5 <= spec.minutes_before <= 30:
        return "config_regression"
    if spec.calibration == "old" and not spec.config_changed:
        return "sensor_drift"
    if spec.memory_trend == "climbing":
        return "resource_exhaustion"
    return "ambiguous"


def spec_from_custom(custom: CustomIncidentSpec, rng: random.Random) -> IncidentSpec:
    """Render exactly the signals the builder asked for; the class decides the fix semantics."""
    network = custom.network == "degraded"
    return IncidentSpec(
        type=classify_custom(custom),
        machine_id=custom.machine,
        depth=-custom.throughput_delta / 100,
        ramp_sim_s=1 if network else rng.randint(300, 600),
        flags=SignalFlags(
            onset="sudden" if network else "gradual",
            config_changed_min_before=custom.minutes_before if custom.config_changed else None,
            calibration_age_days=rng.uniform(31, 60) if custom.calibration == "old" else None,
            phantom_temperature=custom.temperature == "high",
            high_sensor_variance=custom.calibration == "old",
            network_degraded=network,
            memory_climbing=custom.memory_trend == "climbing",
        ),
        error_extra_pct=custom.error_rate,
        packet_loss_pct=rng.uniform(8, 15) if network else 0.0,
        latency_ms=rng.uniform(80, 200) if network else 0.0,
        phantom_delta_c=rng.uniform(28, 45) if custom.temperature == "high" else 0.0,
        variance_high=rng.uniform(0.2, 0.4) if custom.calibration == "old" else 0.0,
    )


def log_vocabularies(flags: SignalFlags) -> tuple[IncidentType, ...]:
    """Log vocabularies an incident emits, driven by its signals rather than its class.

    A custom incident with a config change *and* stale calibration logs both kinds of lines;
    one with no identifying signal only logs generic slowdown lines.
    """
    vocab: list[IncidentType] = []
    if flags.config_changed_min_before is not None:
        vocab.append("config_regression")
    if flags.phantom_temperature or flags.high_sensor_variance:
        vocab.append("sensor_drift")
    if flags.network_degraded:
        vocab.append("network_failure")
    if flags.memory_climbing:
        vocab.append("resource_exhaustion")
    if flags.vision_dropout:
        vocab.append("vision_link_dropout")
    if flags.servo_drift:
        vocab.append("servo_tuning_drift")
    return tuple(vocab) or ("ambiguous",)
