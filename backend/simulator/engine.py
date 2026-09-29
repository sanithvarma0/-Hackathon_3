"""Simulator engine: machine dynamics, incidents, actions, re-degradation (BUILD_PLAN.md 5).

Design:
- Every metric is a closed-form function of sim time (a degradation level L(t) per incident,
  built from ramp segments) plus a seeded mean-reverting random walk, sampled on a fixed
  STEP_SIM_S grid. The same seed and the same sequence of calls give the same history no
  matter how fast the clock runs, so live demo, tests and eval fast-forward all agree.
- Reads advance the world lazily (`tick()` first), so state is consistent without relying on
  a background loop; the API's loop only exists to push SSE updates.
- Ground truth (incident class, true action effects) stays inside the engine. The agent sees
  the world only through `backend.adapters` (5.9).
"""

import json
import random
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from backend.schemas import (
    ACTIONS,
    METRICS,
    PREDEFINED_TYPES,
    SITE_KNOWLEDGE_TYPES,
    Action,
    ActionReceipt,
    ActionRecord,
    Alert,
    CustomIncidentSpec,
    Event,
    GatewayState,
    Incident,
    IncidentStatus,
    IncidentType,
    MachineMetrics,
    MachineStatus,
    Metric,
    MetricPoint,
    PlantState,
    RecoveryObservation,
)
from backend.simulator import db
from backend.simulator.clock import Clock
from backend.simulator.incidents import (
    CORRECT_FIX,
    LOG_TEMPLATES,
    NOISE_LOGS,
    IncidentSpec,
    effect_of,
    generate_spec,
    log_vocabularies,
    render_log,
    spec_from_custom,
)
from backend.simulator.machines import FEEDS, GATEWAYS, LINE, MACHINES, format_version

STEP_SIM_S = 10
BACKFILL_DAYS = 7
BACKFILL_STEP_SIM_S = 900
# World history older than this is pruned (checked every sim hour) so a long-running
# deployment's disk stays bounded. The agent's tools look back at most a day; the backfill
# and sparklines cover 7 days. Incidents, actions and memory are never pruned.
RETENTION_SIM_S = 8 * 86400
PRUNE_EVERY_SIM_S = 3600
# Every controller runs a firmware integrity check every night (routine, so its presence is
# never a giveaway): 02:00 UTC plus a per-machine offset.
FIRMWARE_CHECK_S = 2 * 3600
FIRMWARE_CHECK_OFFSET_S = 600
HEALTHY_RATIO = 0.90  # throughput / baseline at or above this is healthy
CRITICAL_RATIO = 0.70
ACTION_RAMP_SIM_S = 20  # how long an effective fix takes to take hold
RE_DEGRADE_RAMP_SIM_S = 30
MEMORY_PEAK_PCT = 96.0
MEMORY_PRE_ONSET_FRACTION = 0.85  # how far memory has climbed toward peak by onset
MEMORY_CLIMB_DAYS = 3

# Mean-reverting random-walk noise per metric: (sigma per step, reversion factor)
JITTER: dict[str, tuple[float, float]] = {
    "throughput_pct": (0.35, 0.9),
    "oee_pct": (0.25, 0.9),
    "error_rate_pct": (0.08, 0.9),
    "temperature_c": (0.35, 0.95),
    "sensor_variance": (0.003, 0.9),
    "packet_loss_pct": (0.03, 0.8),
    "latency_ms": (0.4, 0.8),
    "memory_pct": (0.4, 0.95),
}

NotificationKind = Literal["incident_detected", "re_degradation", "incident_closed"]


class SimulatorError(Exception):
    """A rejected control-plane or adapter request (guardrail 5). Never a crash."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SimNotification:
    kind: NotificationKind
    incident_id: str
    ts: int
    message: str


@dataclass(frozen=True)
class _Baseline:
    throughput_pct: float
    oee_pct: float
    error_rate_pct: float
    temperature_c: float
    sensor_variance: float
    packet_loss_pct: float
    latency_ms: float
    memory_pct: float


@dataclass(frozen=True)
class _Segment:
    start: float
    target: float  # degradation level L to move to
    ramp: float


@dataclass
class _Fault:
    incident_id: str
    spec: IncidentSpec
    affected: tuple[str, ...]
    onset: int
    depth: dict[str, float]
    segments: list[_Segment]
    effective_action_taken: bool = False
    last_action_ts: int | None = None
    re_degrade_at: int | None = None
    re_degrade_notified: bool = False
    wait_started: int | None = None
    previous_config: dict[str, tuple[int, int, int]] = field(default_factory=dict)

    def level(self, t: float) -> float:
        """Degradation level L(t) in [0, 1] from chronologically ordered ramp segments."""
        prev: _Segment | None = None
        prev_start_value = 0.0
        for seg in self.segments:
            if t < seg.start:
                break
            if prev is not None:
                prev_start_value = _ramp_value(prev, prev_start_value, seg.start)
            prev = seg
        return 0.0 if prev is None else _ramp_value(prev, prev_start_value, t)


def _ramp_value(seg: _Segment, start_value: float, t: float) -> float:
    progress = min(1.0, max(0.0, (t - seg.start) / seg.ramp)) if seg.ramp > 0 else 1.0
    return start_value + (seg.target - start_value) * progress


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%H:%M:%S")


class Simulator:
    def __init__(
        self,
        conn: sqlite3.Connection,
        clock: Clock,
        *,
        seed: int | None = None,
        escalation_penalty_sim_s: int = 1800,
    ) -> None:
        self._conn = conn
        self._clock = clock
        self._seed = seed
        self._escalation_penalty = escalation_penalty_sim_s
        self._lock = threading.RLock()
        self._listeners: list[Callable[[SimNotification], None]] = []
        self._rng = random.Random(seed)
        self._id_offset = 0
        self._init_world(keep_incident_log=True)  # a restart must not erase finished incidents

    # ---- lifecycle --------------------------------------------------------------------------

    def subscribe(self, listener: Callable[[SimNotification], None]) -> None:
        self._listeners.append(listener)

    def set_incident_offset(self, offset: int) -> None:
        """Continue numbering after `offset` (IDs must not repeat while memory persists)."""
        with self._lock:
            self._id_offset = max(0, offset)

    def reset(self) -> None:
        with self._lock:
            self._rng = random.Random(self._seed)
            self._init_world()

    def _init_world(self, *, keep_incident_log: bool = False) -> None:
        if keep_incident_log:
            db.clear(self._conn, db.WORLD_TABLES)
            db.drop_unfinished_incidents(self._conn)
        else:
            db.clear(self._conn)
        rng = self._rng
        now = int(self._clock.now())
        self._last_step = now - now % STEP_SIM_S
        self._last_prune = self._last_step
        self._fault: _Fault | None = None
        self._baseline: dict[str, _Baseline] = {}
        self._jitter: dict[str, dict[str, float]] = {}
        self._current: dict[str, dict[str, float]] = {}
        self._config: dict[str, tuple[int, int, int]] = {}
        self._last_calibration: dict[str, int] = {}

        for mid, m in MACHINES.items():
            self._baseline[mid] = _Baseline(
                throughput_pct=rng.uniform(95, 99),
                oee_pct=rng.uniform(88, 93),
                error_rate_pct=rng.uniform(0.3, 1.8),
                temperature_c=rng.uniform(38, 46),
                sensor_variance=rng.uniform(0.01, 0.05),
                packet_loss_pct=rng.uniform(0.05, 0.4),
                latency_ms=rng.uniform(2, 8),
                memory_pct=rng.uniform(35, 55),
            )
            self._jitter[mid] = dict.fromkeys(METRICS, 0.0)
            self._config[mid] = m.base_config_version
            self._last_calibration[mid] = now - int(rng.uniform(5, 25) * 86400)

        self._backfill(now)
        for mid in MACHINES:
            self._current[mid] = self._sample(mid, self._last_step, advance=True)
            self._write_history(mid, self._last_step)
        self._write_machines()
        self._conn.commit()

    def _backfill(self, now: int) -> None:
        """7 days of realistic history: jittered metrics, routine events, background logs."""
        rng = self._rng
        start = now - BACKFILL_DAYS * 86400
        start -= start % BACKFILL_STEP_SIM_S
        rows = []
        for t in range(start, self._last_step, BACKFILL_STEP_SIM_S):
            for mid in MACHINES:
                v = self._sample(mid, t, advance=True)
                rows.append((t, mid, *(v[k] for k in METRICS)))
                if rng.random() < 0.2:
                    self._log_noise(mid, t)
        self._conn.executemany(
            f"INSERT INTO machine_history (ts, machine_id, {', '.join(METRICS)}) "
            f"VALUES (?, ?, {', '.join('?' * len(METRICS))})",
            rows,
        )

        # Routine changes, so "a config was deployed" is never a giveaway on its own.
        for mid in MACHINES:
            deploy_times = sorted(
                rng.randint(start, now - 6 * 3600) for _ in range(rng.randint(1, 3))
            )
            for ts in deploy_times:
                old = self._config[mid]
                self._config[mid] = (old[0], old[1], old[2] + 1)
                self._event(
                    mid,
                    ts,
                    "config_deployed",
                    f"config {format_version(old)} -> {format_version(self._config[mid])} "
                    f"deployed via change CHG-{rng.randint(4000, 4999)}",
                )
            self._event(
                mid,
                self._last_calibration[mid],
                "calibration",
                "sensor calibration completed (next due in 30 days)",
            )
            if rng.random() < 0.5:
                self._event(
                    mid,
                    rng.randint(start, now - 3600),
                    "machine_restart",
                    "planned restart after preventive maintenance",
                )
        gw = rng.choice(tuple(GATEWAYS))
        self._event(gw, rng.randint(start, now - 86400), "gateway_restart", "firmware update 3.4.2")
        for i, mid in enumerate(MACHINES):
            first = start - start % 86400 + FIRMWARE_CHECK_S + i * FIRMWARE_CHECK_OFFSET_S
            for ts in range(first, now, 86400):
                if ts >= start:
                    self._firmware_check(mid, ts)

    def _firmware_check(self, mid: str, ts: int) -> None:
        self._event(mid, ts, "firmware_check", "nightly controller firmware integrity check: OK")

    # ---- dynamics ---------------------------------------------------------------------------

    def tick(self) -> None:
        """Advance the world to the clock's current time on the fixed step grid."""
        with self._lock:
            now = self._clock.now()
            stepped = False
            while self._last_step + STEP_SIM_S <= now:
                self._last_step += STEP_SIM_S
                self._step(self._last_step)
                stepped = True
            if stepped:
                self._write_machines()
                if self._last_step - self._last_prune >= PRUNE_EVERY_SIM_S:
                    self.prune_history(self._last_step)
                self._conn.commit()

    def prune_history(self, now: int) -> None:
        """Delete machine history, events and logs older than the retention window."""
        with self._lock:
            cutoff = now - RETENTION_SIM_S
            for table in ("machine_history", "events", "logs"):
                self._conn.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))  # noqa: S608
            self._last_prune = now

    def _step(self, t: int) -> None:
        fault = self._fault
        for i, mid in enumerate(MACHINES):
            self._current[mid] = self._sample(mid, t, advance=True)
            self._write_history(mid, t)
            if self._rng.random() < 0.004:
                self._log_noise(mid, t)
            if t % 86400 == FIRMWARE_CHECK_S + i * FIRMWARE_CHECK_OFFSET_S:
                self._firmware_check(mid, t)
        if fault is None:
            return

        level = fault.level(t)
        if level > 0.15:  # symptoms are logged from early in the onset
            for mid in fault.affected:
                if self._rng.random() < 0.25:
                    vocab = self._rng.choice(log_vocabularies(fault.spec.flags))
                    level_name, template = self._rng.choice(LOG_TEMPLATES[vocab])
                    self._log(mid, t, level_name, render_log(template, mid, self._rng))
                if (
                    fault.spec.flags.memory_climbing
                    and self._current[mid]["memory_pct"] > 92
                    and self._rng.random() < 0.05
                ):
                    proc = self._rng.choice(("hmi-cache", "toolpath-svc", "vision-worker"))
                    detail = f"OOM killer terminated {proc} (pid {self._rng.randint(1200, 9800)})"
                    self._event(mid, t, "oom_kill", detail)
                    self._log(mid, t, "ERROR", detail)

        incident = self._incident_row(fault.incident_id)
        if incident["detected_ts"] is None and any(
            self._ratio(mid) < HEALTHY_RATIO for mid in fault.affected
        ):
            self._conn.execute(
                "UPDATE incidents SET detected_ts = ? WHERE id = ?", (t, fault.incident_id)
            )
            self._notify(
                "incident_detected", fault.incident_id, t, "throughput below alert threshold"
            )

        if (
            fault.re_degrade_at is not None
            and not fault.re_degrade_notified
            and t >= fault.re_degrade_at
        ):
            fault.re_degrade_notified = True
            assert fault.last_action_ts is not None
            after = fault.re_degrade_at - fault.last_action_ts
            self._conn.execute(
                "UPDATE actions_log SET re_degraded_after_sim_s = ? "
                "WHERE incident_id = ? AND ts = ?",
                (after, fault.incident_id, fault.last_action_ts),
            )
            if incident["status"] == "verifying":
                self._set_status(fault.incident_id, "open")
            self._notify(
                "re_degradation",
                fault.incident_id,
                t,
                f"condition returned {after} s after the last action",
            )

    def _sample(self, mid: str, t: float, *, advance: bool) -> dict[str, float]:
        base = self._baseline[mid]
        jit = self._jitter[mid]
        if advance:
            for k, (sigma, keep) in JITTER.items():
                jit[k] = jit[k] * keep + self._rng.gauss(0, sigma)

        fault = self._fault
        level = 0.0
        depth = 0.0
        spec: IncidentSpec | None = None
        if fault is not None and mid in fault.affected and t >= fault.onset:
            level = fault.level(t)
            depth = fault.depth[mid]
            spec = fault.spec

        tp = base.throughput_pct * (1 - depth * level) + jit["throughput_pct"]
        err = base.error_rate_pct + jit["error_rate_pct"]
        temp = base.temperature_c + jit["temperature_c"]
        var = base.sensor_variance + jit["sensor_variance"]
        loss = base.packet_loss_pct + jit["packet_loss_pct"]
        lat = base.latency_ms + jit["latency_ms"]
        mem = base.memory_pct + jit["memory_pct"]
        if spec is not None:
            flags = spec.flags
            err += spec.error_extra_pct * level
            if flags.phantom_temperature:
                temp += spec.phantom_delta_c * level
            if flags.high_sensor_variance:
                var += (spec.variance_high - base.sensor_variance) * level
            if flags.network_degraded or flags.vision_dropout:
                loss += spec.packet_loss_pct * level
                lat += spec.latency_ms * level
            if flags.memory_climbing:
                assert fault is not None
                climb = (
                    level
                    if fault.effective_action_taken
                    else MEMORY_PRE_ONSET_FRACTION + (1 - MEMORY_PRE_ONSET_FRACTION) * level
                )
                mem = (
                    base.memory_pct
                    + (MEMORY_PEAK_PCT - base.memory_pct) * climb
                    + jit["memory_pct"]
                )

        tp = _clamp(tp, 0, 100)
        err = _clamp(err, 0, 100)
        oee = base.oee_pct * (tp / base.throughput_pct) - (err - base.error_rate_pct) * 0.5
        return {
            "throughput_pct": round(tp, 2),
            "oee_pct": round(_clamp(oee + jit["oee_pct"], 0, 100), 2),
            "error_rate_pct": round(err, 2),
            "temperature_c": round(temp, 1),
            "sensor_variance": round(_clamp(var, 0.001, 1), 4),
            "packet_loss_pct": round(_clamp(loss, 0, 100), 2),
            "latency_ms": round(_clamp(lat, 0.5, 5000), 1),
            "memory_pct": round(_clamp(mem, 5, 99.5), 1),
        }

    def _ratio(self, mid: str) -> float:
        return self._current[mid]["throughput_pct"] / self._baseline[mid].throughput_pct

    def _status(self, mid: str) -> MachineStatus:
        ratio = self._ratio(mid)
        fault = self._fault
        if (
            fault is not None
            and mid in fault.affected
            and ratio >= HEALTHY_RATIO
            and self._incident_row(fault.incident_id)["status"] == "verifying"
        ):
            return "recovering"
        if ratio >= HEALTHY_RATIO:
            return "healthy"
        if ratio >= CRITICAL_RATIO:
            return "degraded"
        return "critical"

    # ---- control plane: incidents -----------------------------------------------------------

    def trigger_incident(
        self,
        incident_type: IncidentType | None = None,
        machine: str | None = None,
        custom: CustomIncidentSpec | None = None,
        spec_seed: int | None = None,
    ) -> Incident:
        """Start an incident. `spec_seed` (eval harness) makes the incident identical across
        runs whatever happened before it: the simulator's random stream restarts from it, so
        paired memory-ON/OFF runs face the same signature, magnitude, timing and wording."""
        with self._lock:
            self.tick()
            if self._fault is not None:
                raise SimulatorError(
                    "INCIDENT_ACTIVE",
                    f"{self._fault.incident_id} is still open; resolve it first",
                )
            if spec_seed is not None:
                self._rng = random.Random(spec_seed)
            rng = self._rng
            if custom is not None:
                self._require_machine(custom.machine)
                spec = spec_from_custom(custom, rng)
            else:
                if incident_type is None:
                    incident_type = rng.choice(PREDEFINED_TYPES)
                if incident_type not in PREDEFINED_TYPES + SITE_KNOWLEDGE_TYPES:
                    raise SimulatorError("INVALID_TYPE", f"unknown incident type {incident_type!r}")
                if machine is None:
                    machine = self._pick_machine(incident_type)
                self._require_machine(machine)
                spec = generate_spec(incident_type, machine, rng)

            gateway = MACHINES[spec.machine_id].gateway
            affected = GATEWAYS[gateway] if spec.flags.network_degraded else (spec.machine_id,)
            unhealthy = [m for m in affected if self._status(m) != "healthy"]
            if unhealthy:
                raise SimulatorError(
                    "MACHINE_NOT_HEALTHY", f"{', '.join(unhealthy)} is already degraded"
                )

            onset = int(self._clock.now())
            # Next after both the kept incident log and memory (IDs never repeat).
            logged = [
                int(r[0][4:])
                for r in self._conn.execute("SELECT id FROM incidents")
                if str(r[0]).startswith("INC-") and str(r[0])[4:].isdigit()
            ]
            incident_id = f"INC-{max([self._id_offset, *logged]) + 1:03d}"
            fault = _Fault(
                incident_id=incident_id,
                spec=spec,
                affected=affected,
                onset=onset,
                depth={m: _clamp(spec.depth * rng.uniform(0.9, 1.1), 0.05, 0.85) for m in affected},
                segments=[_Segment(onset, 1.0, spec.ramp_sim_s)],
            )
            self._write_preconditions(fault)
            self._conn.execute(
                "INSERT INTO incidents (id, type, machine_id, affected, gateway, signature, "
                "status, onset_ts) VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
                (
                    incident_id,
                    spec.type,
                    spec.machine_id,
                    json.dumps(list(affected)),
                    gateway if spec.flags.network_degraded else None,
                    json.dumps(
                        {
                            "depth": round(spec.depth, 3),
                            "ramp_sim_s": spec.ramp_sim_s,
                            "flags": spec.flags.__dict__,
                        }
                    ),
                    onset,
                ),
            )
            self._fault = fault
            self._conn.commit()
            return self.get_incident(incident_id)

    def _pick_machine(self, incident_type: IncidentType) -> str:
        if incident_type == "network_failure":
            gateways = [
                gw for gw, ms in GATEWAYS.items() if all(self._status(m) == "healthy" for m in ms)
            ]
            if not gateways:
                raise SimulatorError("MACHINE_NOT_HEALTHY", "no gateway with all machines healthy")
            return self._rng.choice(GATEWAYS[self._rng.choice(gateways)])
        healthy = [m for m in MACHINES if self._status(m) == "healthy"]
        if not healthy:
            raise SimulatorError("MACHINE_NOT_HEALTHY", "no healthy machine available")
        return self._rng.choice(healthy)

    def _write_preconditions(self, fault: _Fault) -> None:
        """Write the history the agent's tools must be able to find (5.6 step 4)."""
        rng = self._rng
        flags = fault.spec.flags
        mid = fault.spec.machine_id
        onset = fault.onset
        if flags.config_changed_min_before is not None:
            old = self._config[mid]
            new = (old[0], old[1] + 1, 0)
            fault.previous_config[mid] = old
            self._config[mid] = new
            self._event(
                mid,
                onset - flags.config_changed_min_before * 60,
                "config_deployed",
                f"config {format_version(old)} -> {format_version(new)} "
                f"deployed via change CHG-{rng.randint(4000, 4999)}",
            )
        if flags.calibration_age_days is not None:
            cal_ts = onset - int(flags.calibration_age_days * 86400)
            self._conn.execute(
                "DELETE FROM events WHERE machine_id = ? AND event_type = 'calibration' AND ts > ?",
                (mid, cal_ts),
            )
            self._last_calibration[mid] = cal_ts
            self._event(
                mid, cal_ts, "calibration", "sensor calibration completed (next due in 30 days)"
            )
        if flags.memory_climbing:
            start = onset - MEMORY_CLIMB_DAYS * 86400
            base = self._baseline[mid].memory_pct
            rows = self._conn.execute(
                "SELECT rowid, ts FROM machine_history WHERE machine_id = ? AND ts >= ?",
                (mid, start),
            ).fetchall()
            updates = []
            for row in rows:
                frac = MEMORY_PRE_ONSET_FRACTION * (row["ts"] - start) / (onset - start)
                value = base + (MEMORY_PEAK_PCT - base) * frac + rng.gauss(0, 0.8)
                updates.append((round(_clamp(value, 5, 99.5), 1), row["rowid"]))
            self._conn.executemany(
                "UPDATE machine_history SET memory_pct = ? WHERE rowid = ?", updates
            )
            detail = f"OOM killer terminated hmi-cache (pid {rng.randint(1200, 9800)})"
            self._event(mid, onset - rng.randint(600, 3600), "oom_kill", detail)
        if flags.network_degraded:
            gw = MACHINES[mid].gateway
            self._event(gw, onset, "link_flap", f"uplink port 2 on {gw} flapped (3 transitions)")

    def begin_wait(self, incident_id: str) -> None:
        """The agent has recommended; a human now decides. Human wait is excluded from MTTR."""
        with self._lock:
            fault = self._require_active(incident_id)
            self.tick()
            fault.wait_started = int(self._clock.now())
            self._set_status(incident_id, "awaiting_action")
            self._conn.commit()

    def execute_action(
        self, incident_id: str, action: Action, executed_by: str = "agent"
    ) -> ActionReceipt:
        with self._lock:
            fault = self._require_active(incident_id)
            if action not in ACTIONS:
                raise SimulatorError("INVALID_ACTION", f"{action!r} is not a whitelisted action")
            self.tick()
            now = int(self._clock.now())
            incident = self._incident_row(incident_id)
            if fault.wait_started is not None:
                self._conn.execute(
                    "UPDATE incidents SET human_wait_sim_s = human_wait_sim_s + ? WHERE id = ?",
                    (now - fault.wait_started, incident_id),
                )
                fault.wait_started = None
            attempt = (
                self._conn.execute(
                    "SELECT COUNT(*) FROM actions_log WHERE incident_id = ?", (incident_id,)
                ).fetchone()[0]
                + 1
            )
            # A new action supersedes anything still scheduled from the previous one.
            fault.segments = [s for s in fault.segments if s.start <= now]
            fault.re_degrade_at = None
            fault.re_degrade_notified = False
            mid = incident["machine_id"]

            if action == "ESCALATE_HUMAN":
                self._record_action(
                    now, incident_id, attempt, action, "escalated", None, executed_by
                )
                self._event(mid, now, "escalation", f"{incident_id} escalated to on-call engineer")
                self._close(incident_id, "escalated", action, penalty=self._escalation_penalty)
                self._record_engineer_fix(incident_id, fault)
                return ActionReceipt(
                    incident_id=incident_id,
                    action=action,
                    executed_ts=now,
                    message=f"{incident_id} escalated to the on-call engineer",
                )

            spec = effect_of(fault.spec.type, action)
            recovery_pct: float | None = None
            if spec.effect == "full_recovery":
                fault.segments.append(_Segment(now, 0.0, ACTION_RAMP_SIM_S))
                fault.effective_action_taken = True
            elif spec.effect == "partial_recovery":
                assert spec.recovery_pct is not None and spec.re_degrade_after_sim_s is not None
                recovery_pct = self._rng.uniform(*spec.recovery_pct)
                partial_level = min(
                    fault.level(now),
                    max(
                        0.0,
                        (1 - recovery_pct / self._baseline[mid].throughput_pct) / fault.depth[mid],
                    ),
                )
                delay = self._rng.randint(*spec.re_degrade_after_sim_s)
                fault.segments.append(_Segment(now, partial_level, ACTION_RAMP_SIM_S))
                fault.segments.append(_Segment(now + delay, 1.0, RE_DEGRADE_RAMP_SIM_S))
                fault.re_degrade_at = now + delay
                fault.effective_action_taken = True
            fault.last_action_ts = now

            message = self._apply_action_side_effects(fault, action, now)
            self._record_action(
                now, incident_id, attempt, action, spec.effect, recovery_pct, executed_by
            )
            self._set_status(incident_id, "verifying")
            self._conn.commit()
            return ActionReceipt(
                incident_id=incident_id, action=action, executed_ts=now, message=message
            )

    def _apply_action_side_effects(self, fault: _Fault, action: Action, now: int) -> str:
        """What the action visibly does to the world, whether or not it fixes the incident."""
        mid = fault.spec.machine_id
        if action == "ROLLBACK_CONFIG":
            current = self._config[mid]
            previous = fault.previous_config.pop(
                mid, (current[0], current[1], max(0, current[2] - 1))
            )
            self._config[mid] = previous
            detail = f"config {format_version(current)} -> {format_version(previous)} (rollback)"
            self._event(mid, now, "config_rollback", detail)
            return f"Config on {mid} rolled back: {detail}"
        if action == "RESTART_MACHINE":
            self._event(
                mid, now, "machine_restart", "controller restart requested by incident response"
            )
            return f"{mid} controller restarted"
        if action == "RECALIBRATE_SENSOR":
            self._last_calibration[mid] = now
            self._event(mid, now, "calibration", "sensor recalibration completed")
            return f"Sensors on {mid} recalibrated"
        if action == "RESTART_GATEWAY":
            gw = MACHINES[mid].gateway
            self._event(
                gw, now, "gateway_restart", "gateway restart requested by incident response"
            )
            return f"Gateway {gw} restarted"
        if action == "CLEAR_CACHE":
            self._event(
                mid, now, "cache_cleared", "controller caches cleared (hmi-cache, toolpath)"
            )
            return f"Controller caches on {mid} cleared"
        raise AssertionError(action)

    def observe_recovery(self, incident_id: str, window_sim_s: int) -> RecoveryObservation:
        """Did the affected machines recover after the last action, and did it hold?"""
        with self._lock:
            fault = self._require_active(incident_id)
            if fault.last_action_ts is None:
                raise SimulatorError("NO_ACTION", f"no action has been executed on {incident_id}")
            self.tick()
            now = int(self._clock.now())
            start = fault.last_action_ts
            end = min(now, start + window_sim_s)
            placeholders = ", ".join("?" * len(fault.affected))
            rows = self._conn.execute(
                f"SELECT ts, machine_id, throughput_pct FROM machine_history "  # noqa: S608
                f"WHERE machine_id IN ({placeholders}) AND ts > ? AND ts <= ? ORDER BY ts",
                (*fault.affected, start + ACTION_RAMP_SIM_S, end),
            ).fetchall()
            worst_by_ts: dict[int, float] = {}
            for row in rows:
                ratio = row["throughput_pct"] / self._baseline[row["machine_id"]].throughput_pct
                worst_by_ts[row["ts"]] = min(worst_by_ts.get(row["ts"], 1e9), ratio)
            ratios = [worst_by_ts[t] for t in sorted(worst_by_ts)]
            recovered = any(r >= HEALTHY_RATIO for r in ratios)
            primary = fault.spec.machine_id
            primary_tp = [r["throughput_pct"] for r in rows if r["machine_id"] == primary]
            current_tp = self._current[primary]["throughput_pct"]
            return RecoveryObservation(
                incident_id=incident_id,
                window_sim_s=window_sim_s,
                elapsed_sim_s=now - start,
                complete=now - start >= window_sim_s,
                recovered=recovered,
                held=bool(ratios) and all(r >= HEALTHY_RATIO for r in ratios),
                peak_throughput_pct=max(primary_tp, default=current_tp),
                min_throughput_pct=min(primary_tp, default=current_tp),
                current_throughput_pct=current_tp,
            )

    def close_incident(self, incident_id: str) -> Incident:
        """Close a verified incident as resolved (called by the orchestrator after verify)."""
        with self._lock:
            self._require_active(incident_id)
            self.tick()
            if self._incident_row(incident_id)["status"] != "verifying":
                raise SimulatorError(
                    "NOT_VERIFYING", f"{incident_id} has no action awaiting verification"
                )
            last = self._conn.execute(
                "SELECT action FROM actions_log WHERE incident_id = ? ORDER BY ts DESC LIMIT 1",
                (incident_id,),
            ).fetchone()
            self._close(incident_id, "resolved", last["action"], penalty=0)
            return self.get_incident(incident_id)

    def _record_engineer_fix(self, incident_id: str, fault: _Fault) -> None:
        """The on-call engineer diagnoses and fixes an escalated incident and notes it on the
        ticket. Ambiguous incidents are fixed outside the agent's action set."""
        fix = CORRECT_FIX[fault.spec.type]
        mid = fault.spec.machine_id
        gw = MACHINES[mid].gateway
        site_notes: dict[str, str] = {  # plant knowledge the signals alone don't reveal
            "vision_link_dropout": (
                f"traced the vision timeouts on {mid} to the camera's PoE port on the {gw} "
                f"switch, which drops under load — a known issue on this line. Restarting "
                f"gateway {gw} re-powers the camera (replacement switch on order)"
            ),
            "servo_tuning_drift": (
                f"found {mid}'s servo tuning table out of sync after the nightly firmware "
                f"check: the controller keeps a stale cached copy until a cold restart. "
                f"Restarted the controller ({mid}); rollback and recalibration don't help"
            ),
        }
        notes: dict[str, str] = {
            "ROLLBACK_CONFIG": f"rolled back the latest config deploy on {mid}",
            "RECALIBRATE_SENSOR": f"recalibrated the sensors on {mid}; readings were drifting",
            "RESTART_GATEWAY": f"restarted gateway {MACHINES[mid].gateway}",
            "CLEAR_CACHE": f"cleared the controller caches on {mid}; memory was exhausted",
        }
        if fix == "ESCALATE_HUMAN":
            action, note = None, f"replaced a failing controller I/O module on {mid}"
        elif fault.spec.type in site_notes:
            action, note = fix, site_notes[fault.spec.type]
        else:
            action, note = fix, notes[fix]
        self._conn.execute(
            "UPDATE incidents SET engineer_action = ?, engineer_note = ? WHERE id = ?",
            (action, f"On-call engineer {note}.", incident_id),
        )
        self._conn.commit()

    def _close(
        self, incident_id: str, status: IncidentStatus, action: Action, *, penalty: int
    ) -> None:
        now = int(self._clock.now())
        row = self._incident_row(incident_id)
        mttr = now - row["onset_ts"] - row["human_wait_sim_s"] + penalty
        self._conn.execute(
            "UPDATE incidents SET status = ?, resolved_ts = ?, resolution_action = ?, "
            "mttr_sim_s = ? WHERE id = ?",
            (status, now, action, mttr, incident_id),
        )
        affected = self._fault.affected if self._fault is not None else ()
        self._fault = None
        for mid in affected:  # the fix is in: refresh readings now, not at the next tick
            self._current[mid] = self._sample(mid, self._last_step, advance=False)
        self._write_machines()
        self._conn.commit()
        self._notify("incident_closed", incident_id, now, status)

    def ignore(self, incident_id: str) -> None:
        """IGNORE consequence (5.6): the condition worsens now; the incident stays open."""
        with self._lock:
            fault = self._require_active(incident_id)
            self.tick()
            now = int(self._clock.now())
            for m in fault.affected:
                fault.depth[m] = min(0.85, fault.depth[m] + self._rng.uniform(0.10, 0.20))
            fault.segments = [s for s in fault.segments if s.start <= now]
            fault.segments.append(_Segment(now, 1.0, 1))
            fault.re_degrade_at = None
            for m in fault.affected:
                self._log(m, now, "ALARM", "alarm acknowledged without action; condition worsening")
            self._conn.commit()

    # ---- agent-facing reads (exposed via backend.adapters) ----------------------------------

    def get_machine_metrics(self, machine_id: str) -> MachineMetrics:
        with self._lock:
            self._require_machine(machine_id)
            self.tick()
            return self._metrics(machine_id)

    def get_metric_history(
        self, machine_id: str, metric: Metric, window_hours: float, max_points: int = 60
    ) -> list[MetricPoint]:
        with self._lock:
            self._require_machine(machine_id)
            if metric not in METRICS:
                raise SimulatorError("INVALID_METRIC", f"unknown metric {metric!r}")
            self.tick()
            since = int(self._clock.now() - window_hours * 3600)
            rows = self._conn.execute(
                f"SELECT ts, {metric} AS value FROM machine_history "  # noqa: S608
                "WHERE machine_id = ? AND ts >= ? ORDER BY ts",
                (machine_id, since),
            ).fetchall()
            if len(rows) <= max_points:
                return [MetricPoint(ts=r["ts"], value=r["value"]) for r in rows]
            # Downsample by averaging equal-sized buckets: keeps tool output small for the LLM.
            size = len(rows) / max_points
            points = []
            for i in range(max_points):
                bucket = rows[int(i * size) : int((i + 1) * size)]
                points.append(
                    MetricPoint(
                        ts=bucket[-1]["ts"],
                        value=round(sum(r["value"] for r in bucket) / len(bucket), 3),
                    )
                )
            return points

    def get_recent_events(self, machine_id: str, window_minutes: float) -> list[Event]:
        with self._lock:
            self._require_machine(machine_id)
            self.tick()
            now = self._clock.now()
            rows = self._conn.execute(
                "SELECT ts, machine_id, event_type, detail FROM events "
                "WHERE machine_id IN (?, ?) AND ts >= ? AND ts <= ? ORDER BY ts",
                (machine_id, MACHINES[machine_id].gateway, int(now - window_minutes * 60), now),
            ).fetchall()
            return [Event(**dict(r)) for r in rows]

    def get_error_logs(self, machine_id: str, window_minutes: float, limit: int = 50) -> list[str]:
        """WARN / ERROR / ALARM lines, oldest first (INFO chatter is not returned)."""
        with self._lock:
            self._require_machine(machine_id)
            self.tick()
            now = self._clock.now()
            rows = self._conn.execute(
                "SELECT ts, level, source, message FROM logs WHERE machine_id = ? "
                "AND level != 'INFO' AND ts >= ? AND ts <= ? ORDER BY ts DESC LIMIT ?",
                (machine_id, int(now - window_minutes * 60), now, limit),
            ).fetchall()
            return [
                f"{_fmt_ts(r['ts'])} {r['level']:<5} {r['source']}: {r['message']}"
                for r in reversed(rows)
            ]

    # ---- dashboard / control-plane reads ----------------------------------------------------

    def plant_state(self) -> PlantState:
        with self._lock:
            self.tick()
            machines = tuple(self._metrics(m) for m in MACHINES)
            tp = {m.machine_id: m.throughput_pct for m in machines}
            # Line-level flow: each stage passes at most what it receives (5.2).
            flow_in: dict[str, float] = {}
            upstream = 100.0
            for m in LINE:
                inbound = upstream
                for feeder, fed in FEEDS.items():
                    if m in fed:
                        inbound = min(inbound, tp[feeder])
                flow_in[m] = inbound
                upstream = min(inbound, tp[m])
            starved = tuple(m for m in LINE if flow_in[m] < tp[m] - 5)
            gateways = []
            for gw, members in GATEWAYS.items():
                loss = max(self._current[m]["packet_loss_pct"] for m in members)
                status: MachineStatus = (
                    "critical" if loss > 5 else "degraded" if loss > 2 else "healthy"
                )
                gateways.append(
                    GatewayState(
                        gateway_id=gw,
                        machines=members,
                        status=status,
                        packet_loss_pct=round(loss, 2),
                    )
                )
            return PlantState(
                ts=self._last_step,
                machines=machines,
                starved=starved,
                gateways=tuple(gateways),
                line_throughput_pct=round(upstream, 2),
                oee_pct=round(sum(m.oee_pct for m in machines) / len(machines), 2),
                alerts=sum(1 for m in machines if m.status in ("degraded", "critical")),
                active_incident_id=self._fault.incident_id if self._fault else None,
            )

    def alert(self, incident_id: str) -> Alert:
        """What monitoring shows when the incident fires: symptoms only, no diagnosis."""
        with self._lock:
            self.tick()
            row = self._incident_row(incident_id)
            mid = row["machine_id"]
            m = MACHINES[mid]
            return Alert(
                incident_id=incident_id,
                machine_id=mid,
                machine_name=m.name,
                machine_profile=m.profile,
                detected_ts=row["detected_ts"] or self._last_step,
                throughput_pct=self._current[mid]["throughput_pct"],
                nominal_throughput_pct=round(self._baseline[mid].throughput_pct, 1),
                alerting_machines=tuple(
                    x for x in MACHINES if self._status(x) in ("degraded", "critical")
                ),
            )

    def active_incident(self) -> Incident | None:
        with self._lock:
            return self.get_incident(self._fault.incident_id) if self._fault else None

    def get_incident(self, incident_id: str) -> Incident:
        with self._lock:
            row = self._incident_row(incident_id)
            return Incident(
                id=row["id"],
                type=row["type"],
                machine_id=row["machine_id"],
                affected=tuple(json.loads(row["affected"])),
                gateway=row["gateway"],
                status=row["status"],
                onset_ts=row["onset_ts"],
                detected_ts=row["detected_ts"],
                resolved_ts=row["resolved_ts"],
                human_wait_sim_s=row["human_wait_sim_s"],
                resolution_action=row["resolution_action"],
                mttr_sim_s=row["mttr_sim_s"],
                engineer_action=row["engineer_action"],
                engineer_note=row["engineer_note"],
            )

    def list_incidents(self) -> list[Incident]:
        with self._lock:
            ids = [
                r["id"] for r in self._conn.execute("SELECT id FROM incidents ORDER BY onset_ts")
            ]
            return [self.get_incident(i) for i in ids]

    def action_log(self, incident_id: str) -> list[ActionRecord]:
        """Ground truth for evaluation and the Memory Browser — never given to the agent."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM actions_log WHERE incident_id = ? ORDER BY ts", (incident_id,)
            ).fetchall()
            return [ActionRecord(**dict(r)) for r in rows]

    # ---- helpers ----------------------------------------------------------------------------

    def _metrics(self, mid: str) -> MachineMetrics:
        m = MACHINES[mid]
        v = self._current[mid]
        return MachineMetrics(
            machine_id=mid,
            name=m.name,
            profile=m.profile,
            gateway=m.gateway,
            status=self._status(mid),
            config_version=format_version(self._config[mid]),
            calibration_age_days=round((self._last_step - self._last_calibration[mid]) / 86400, 1),
            ts=self._last_step,
            **v,
        )

    def _require_machine(self, machine_id: str) -> None:
        if machine_id not in MACHINES:
            raise SimulatorError("UNKNOWN_MACHINE", f"unknown machine {machine_id!r}")

    def _require_active(self, incident_id: str) -> _Fault:
        exists = self._conn.execute(
            "SELECT 1 FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()
        if exists is None:
            raise SimulatorError("INCIDENT_NOT_FOUND", f"no incident {incident_id}")
        if self._fault is None or self._fault.incident_id != incident_id:
            raise SimulatorError("INCIDENT_CLOSED", f"{incident_id} is already closed")
        return self._fault

    def _incident_row(self, incident_id: str) -> sqlite3.Row:
        row = self._conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        if row is None:
            raise SimulatorError("INCIDENT_NOT_FOUND", f"no incident {incident_id}")
        return row  # type: ignore[no-any-return]

    def _set_status(self, incident_id: str, status: IncidentStatus) -> None:
        self._conn.execute("UPDATE incidents SET status = ? WHERE id = ?", (status, incident_id))

    def _record_action(
        self,
        ts: int,
        incident_id: str,
        attempt: int,
        action: Action,
        effect: str,
        recovery_pct: float | None,
        executed_by: str,
    ) -> None:
        self._conn.execute(
            "INSERT INTO actions_log (ts, incident_id, attempt, action, effect, recovery_pct, "
            "executed_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                ts,
                incident_id,
                attempt,
                action,
                effect,
                None if recovery_pct is None else round(recovery_pct, 1),
                executed_by,
            ),
        )

    def _write_history(self, mid: str, t: int) -> None:
        v = self._current[mid]
        self._conn.execute(
            f"INSERT INTO machine_history (ts, machine_id, {', '.join(METRICS)}) "
            f"VALUES (?, ?, {', '.join('?' * len(METRICS))})",
            (t, mid, *(v[k] for k in METRICS)),
        )

    def _write_machines(self) -> None:
        for mid, m in MACHINES.items():
            v = self._current[mid]
            self._conn.execute(
                f"INSERT OR REPLACE INTO machines (id, name, profile, gateway, status, "
                f"{', '.join(METRICS)}, config_version, last_calibration_ts, updated_ts) "
                f"VALUES (?, ?, ?, ?, ?, {', '.join('?' * len(METRICS))}, ?, ?, ?)",
                (
                    mid,
                    m.name,
                    m.profile,
                    m.gateway,
                    self._status(mid),
                    *(v[k] for k in METRICS),
                    format_version(self._config[mid]),
                    self._last_calibration[mid],
                    self._last_step,
                ),
            )

    def _event(self, machine_id: str, ts: int, event_type: str, detail: str) -> None:
        self._conn.execute(
            "INSERT INTO events (ts, machine_id, event_type, detail) VALUES (?, ?, ?, ?)",
            (ts, machine_id, event_type, detail),
        )

    def _log(self, machine_id: str, ts: int, level: str, message: str) -> None:
        self._conn.execute(
            "INSERT INTO logs (ts, machine_id, level, source, message) VALUES (?, ?, ?, ?, ?)",
            (ts, machine_id, level, MACHINES[machine_id].controller, message),
        )

    def _log_noise(self, machine_id: str, ts: int) -> None:
        level, template = self._rng.choice(NOISE_LOGS)
        self._log(machine_id, ts, level, render_log(template, machine_id, self._rng))

    def _notify(self, kind: NotificationKind, incident_id: str, ts: int, message: str) -> None:
        note = SimNotification(kind=kind, incident_id=incident_id, ts=ts, message=message)
        for listener in self._listeners:
            listener(note)
