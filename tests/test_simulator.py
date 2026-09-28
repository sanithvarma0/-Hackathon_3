"""Simulator semantics (BUILD_PLAN.md 5). Every test runs on a manual clock with a fixed seed."""

import time

import pytest

from backend.schemas import ACTIONS, PREDEFINED_TYPES, CustomIncidentSpec, Effect, IncidentType
from backend.simulator import ManualClock, RealtimeClock, Simulator, SimulatorError
from backend.simulator.db import connect
from backend.simulator.engine import HEALTHY_RATIO
from backend.simulator.incidents import EFFECTS, classify_custom, effect_of
from backend.simulator.machines import GATEWAYS, LINE, MACHINES, downstream
from tests.conftest import World

VERIFY_WINDOW = 180
FULLY_DEGRADED_S = 700  # longest onset ramp (600 s) plus margin

AMBIGUOUS = CustomIncidentSpec(machine="M1", throughput_delta=-30)


def observed_effect(world: World, incident_id: str) -> Effect:
    """What an honest observer concludes after watching one verify window."""
    world.advance(VERIFY_WINDOW + 10)
    obs = world.adapter.observe_recovery(incident_id, VERIFY_WINDOW)
    assert obs.complete
    if obs.held:
        return "full_recovery"
    return "partial_recovery" if obs.recovered else "no_effect"


def trigger(world: World, incident_type: IncidentType, machine: str = "M3") -> str:
    if incident_type == "ambiguous":
        return world.sim.trigger_incident(custom=AMBIGUOUS).id
    if incident_type == "network_failure":
        machine = "M1"
    return world.sim.trigger_incident(incident_type, machine).id


# ---- clock & topology ------------------------------------------------------------------------


def test_manual_clock_only_moves_forward():
    clock = ManualClock(100.0)
    clock.advance(5)
    assert clock.now() == 105.0
    with pytest.raises(ValueError):
        clock.advance(-1)


def test_realtime_clock_runs_at_speed():
    clock = RealtimeClock(speed=100, start=0)
    time.sleep(0.05)
    assert 3 <= clock.now() <= 20


def test_topology():
    assert downstream("M1") == ("M2", "M3", "M4")
    assert downstream("M5") == ("M2", "M3", "M4")
    assert downstream("M4") == ()
    assert sorted(m for ms in GATEWAYS.values() for m in ms) == sorted(MACHINES)
    assert set(LINE) | {"M5"} == set(MACHINES)


# ---- healthy world ---------------------------------------------------------------------------


def test_world_starts_healthy_with_seven_days_of_history(world: World):
    state = world.sim.plant_state()
    assert all(m.status == "healthy" for m in state.machines)
    assert all(93 <= m.throughput_pct <= 100 for m in state.machines)
    assert state.alerts == 0 and state.active_incident_id is None
    history = world.adapter.get_metric_history("M1", "throughput_pct", 24 * 7)
    assert len(history) == 60  # downsampled for the LLM
    assert history[-1].ts - history[0].ts > 6 * 86400


def test_every_machine_has_routine_config_deploys(world: World):
    """Decoys: a config deploy in the history is never a giveaway on its own."""
    for mid in MACHINES:
        events = world.adapter.get_recent_events(mid, 24 * 60)
        week = world.sim._conn.execute(
            "SELECT COUNT(*) FROM events WHERE machine_id = ? AND event_type = 'config_deployed'",
            (mid,),
        ).fetchone()[0]
        assert week >= 1
        assert all(
            e.event_type != "config_deployed" or e.ts < world.clock.now() - 6 * 3600 for e in events
        )


def test_same_seed_same_world():
    a, b = World(seed=11), World(seed=11)
    for w in (a, b):
        w.sim.trigger_incident("sensor_drift", "M2")
        w.advance(300)
    assert a.adapter.get_machine_metrics("M2") == b.adapter.get_machine_metrics("M2")
    assert a.adapter.get_error_logs("M2", 60) == b.adapter.get_error_logs("M2", 60)


# ---- effects matrix: every cell --------------------------------------------------------------


MATRIX = [
    (t, a) for t in (*PREDEFINED_TYPES, "ambiguous") for a in ACTIONS if a != "ESCALATE_HUMAN"
]


@pytest.mark.parametrize(("incident_type", "action"), MATRIX)
def test_effects_matrix(incident_type: IncidentType, action):
    world = World()
    incident_id = trigger(world, incident_type)
    world.advance(FULLY_DEGRADED_S)
    world.adapter.execute_action(incident_id, action)
    assert observed_effect(world, incident_id) == effect_of(incident_type, action).effect
    truth = world.sim.action_log(incident_id)[-1]
    assert truth.effect == effect_of(incident_type, action).effect


def test_every_class_has_exactly_one_full_fix():
    for incident_type, row in EFFECTS.items():
        fixes = [a for a, spec in row.items() if spec.effect == "full_recovery"]
        assert len(fixes) == (0 if incident_type == "ambiguous" else 1)


@pytest.mark.parametrize(
    ("incident_type", "delay_range"),
    [("config_regression", (60, 120)), ("resource_exhaustion", (120, 150))],
)
def test_trap_fix_looks_fixed_then_re_degrades(incident_type, delay_range):
    world = World()
    incident_id = trigger(world, incident_type)
    world.advance(FULLY_DEGRADED_S)
    world.adapter.execute_action(incident_id, "RESTART_MACHINE")
    world.advance(40)
    assert world.adapter.get_machine_metrics("M3").status == "recovering"  # looks fixed
    world.advance(VERIFY_WINDOW)
    assert [n.kind for n in world.notes].count("re_degradation") == 1
    after = world.sim.action_log(incident_id)[-1].re_degraded_after_sim_s
    assert after is not None and delay_range[0] <= after <= delay_range[1]
    assert world.sim.get_incident(incident_id).status == "open"


def test_new_action_cancels_pending_re_degradation(world: World):
    incident_id = trigger(world, "config_regression")
    world.advance(FULLY_DEGRADED_S)
    world.adapter.execute_action(incident_id, "RESTART_MACHINE")
    world.advance(30)
    world.adapter.execute_action(incident_id, "ROLLBACK_CONFIG")
    world.advance(VERIFY_WINDOW + 10)
    assert "re_degradation" not in [n.kind for n in world.notes]
    assert world.adapter.observe_recovery(incident_id, VERIFY_WINDOW).held


# ---- signals: what the agent's tools can find -------------------------------------------------


def test_config_regression_signals(world: World):
    incident = world.sim.trigger_incident("config_regression", "M4")
    world.advance(FULLY_DEGRADED_S)
    deploys = [
        e for e in world.adapter.get_recent_events("M4", 60) if e.event_type == "config_deployed"
    ]
    assert len(deploys) == 1
    minutes_before = (incident.onset_ts - deploys[0].ts) / 60
    assert 5 <= minutes_before <= 20
    assert any(
        "->" in line or "timeout" in line or "jitter" in line or "%" in line
        for line in world.adapter.get_error_logs("M4", 15)
    )


def test_sensor_drift_signals(world: World):
    world.sim.trigger_incident("sensor_drift", "M2")
    world.advance(FULLY_DEGRADED_S)
    m = world.adapter.get_machine_metrics("M2")
    assert m.calibration_age_days > 30
    assert m.sensor_variance > 0.15
    assert m.temperature_c > 65  # phantom reading
    assert not [
        e for e in world.adapter.get_recent_events("M2", 30) if e.event_type == "config_deployed"
    ]


def test_network_failure_hits_whole_gateway_suddenly(world: World):
    world.sim.trigger_incident("network_failure", "M3")  # GW-B: M3, M4
    world.advance(10)
    for mid in GATEWAYS["GW-B"]:
        m = world.adapter.get_machine_metrics(mid)
        assert m.status in ("degraded", "critical")
        assert m.packet_loss_pct > 5
    for mid in GATEWAYS["GW-A"]:
        assert world.adapter.get_machine_metrics(mid).status == "healthy"
    assert world.notes[0].kind == "incident_detected"
    gw = [g for g in world.sim.plant_state().gateways if g.gateway_id == "GW-B"][0]
    assert gw.status == "critical"


def test_resource_exhaustion_has_three_day_memory_climb(world: World):
    world.sim.trigger_incident("resource_exhaustion", "M5")
    world.advance(FULLY_DEGRADED_S)
    points = world.adapter.get_metric_history("M5", "memory_pct", 72)
    first_day = [p.value for p in points[:20]]
    last_day = [p.value for p in points[-20:]]
    assert sum(last_day) / 20 - sum(first_day) / 20 > 15
    assert any(e.event_type == "oom_kill" for e in world.adapter.get_recent_events("M5", 120))


def test_detection_happens_when_throughput_crosses_threshold(world: World):
    incident = world.sim.trigger_incident("config_regression", "M1")
    waited = world.advance_until(
        lambda: world.sim.get_incident(incident.id).detected_ts is not None
    )
    assert 0 < waited <= 600
    ratio = world.sim._ratio("M1")
    assert ratio < HEALTHY_RATIO


# ---- custom builder --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (dict(network="degraded", config_changed=True), "network_failure"),
        (dict(config_changed=True, minutes_before=12), "config_regression"),
        (dict(config_changed=True, minutes_before=45), "ambiguous"),
        (dict(calibration="old"), "sensor_drift"),
        (dict(calibration="old", config_changed=True, minutes_before=45), "ambiguous"),
        (dict(memory_trend="climbing"), "resource_exhaustion"),
        (dict(), "ambiguous"),
    ],
)
def test_custom_classifier(spec, expected):
    assert classify_custom(CustomIncidentSpec(machine="M1", **spec)) == expected


def test_custom_incident_renders_requested_signals(world: World):
    custom = CustomIncidentSpec(
        machine="M2",
        config_changed=True,
        minutes_before=8,
        throughput_delta=-40,
        temperature="high",
    )
    incident = world.sim.trigger_incident(custom=custom)
    assert incident.type == "config_regression"
    world.advance(FULLY_DEGRADED_S)
    m = world.adapter.get_machine_metrics("M2")
    assert m.temperature_c > 65  # requested high temperature, even though class is config
    assert 50 <= m.throughput_pct <= 65


def test_ambiguous_incident_only_escalation_works(world: World):
    incident_id = world.sim.trigger_incident(custom=AMBIGUOUS).id
    world.advance(FULLY_DEGRADED_S)
    world.adapter.execute_action(incident_id, "ESCALATE_HUMAN")
    assert world.sim.get_incident(incident_id).status == "escalated"


# ---- lifecycle, MTTR, escalation, ignore ------------------------------------------------------


def test_mttr_excludes_human_wait(world: World):
    incident = world.sim.trigger_incident("config_regression", "M1")
    world.advance(600)
    world.sim.begin_wait(incident.id)
    world.advance(300)  # the human takes five minutes to click
    world.adapter.execute_action(incident.id, "ROLLBACK_CONFIG")
    world.advance(VERIFY_WINDOW)
    closed = world.sim.close_incident(incident.id)
    assert closed.status == "resolved"
    assert closed.human_wait_sim_s == 300
    assert closed.mttr_sim_s == 600 + VERIFY_WINDOW
    assert closed.resolution_action == "ROLLBACK_CONFIG"


def test_escalation_closes_with_penalty(world: World):
    incident = world.sim.trigger_incident("sensor_drift", "M2")
    world.advance(400)
    world.adapter.execute_action(incident.id, "ESCALATE_HUMAN")
    closed = world.sim.get_incident(incident.id)
    assert closed.status == "escalated"
    assert closed.mttr_sim_s == 400 + 1800
    assert world.sim.active_incident() is None
    world.advance(30)
    assert world.adapter.get_machine_metrics("M2").status == "healthy"


def test_ignore_worsens_the_incident_but_keeps_it_open(world: World):
    incident = world.sim.trigger_incident("config_regression", "M1")
    world.advance(FULLY_DEGRADED_S)
    before = world.adapter.get_machine_metrics("M1").throughput_pct
    world.sim.begin_wait(incident.id)
    world.sim.ignore(incident.id)
    world.advance(20)
    after = world.adapter.get_machine_metrics("M1").throughput_pct
    assert before - after > 5
    assert world.sim.get_incident(incident.id).status == "awaiting_action"


def test_line_starvation_is_line_level_not_a_machine_fault(world: World):
    world.sim.trigger_incident("config_regression", "M2")
    world.advance(FULLY_DEGRADED_S)
    state = world.sim.plant_state()
    assert state.line_throughput_pct < 80
    assert "M3" in state.starved and "M4" in state.starved
    statuses = {m.machine_id: m.status for m in state.machines}
    assert statuses["M3"] == statuses["M4"] == "healthy"  # agent sees no second incident


def test_only_one_active_incident(world: World):
    world.sim.trigger_incident("config_regression", "M1")
    with pytest.raises(SimulatorError) as e:
        world.sim.trigger_incident("sensor_drift", "M2")
    assert e.value.code == "INCIDENT_ACTIVE"


@pytest.mark.parametrize(
    ("call", "code"),
    [
        (lambda s: s.trigger_incident("config_regression", "M9"), "UNKNOWN_MACHINE"),
        (lambda s: s.trigger_incident("meteor_strike", "M1"), "INVALID_TYPE"),
        (lambda s: s.execute_action("INC-404", "RESTART_MACHINE"), "INCIDENT_NOT_FOUND"),
        (lambda s: s.get_metric_history("M1", "vibes", 1), "INVALID_METRIC"),
    ],
)
def test_invalid_requests_are_structured_errors(world: World, call, code):
    with pytest.raises(SimulatorError) as e:
        call(world.sim)
    assert e.value.code == code


def test_incident_lifecycle_errors(world: World):
    incident = world.sim.trigger_incident("config_regression", "M1")
    with pytest.raises(SimulatorError) as e:
        world.sim.observe_recovery(incident.id, VERIFY_WINDOW)
    assert e.value.code == "NO_ACTION"
    with pytest.raises(SimulatorError) as e:
        world.sim.close_incident(incident.id)
    assert e.value.code == "NOT_VERIFYING"
    with pytest.raises(SimulatorError) as e:
        world.sim.execute_action(incident.id, "REBOOT_UNIVERSE")  # type: ignore[arg-type]
    assert e.value.code == "INVALID_ACTION"
    world.adapter.execute_action(incident.id, "ESCALATE_HUMAN")
    with pytest.raises(SimulatorError) as e:
        world.sim.execute_action(incident.id, "ROLLBACK_CONFIG")
    assert e.value.code == "INCIDENT_CLOSED"


def test_incident_ids_are_sequential_and_persisted(tmp_path):
    clock = ManualClock(1_790_000_000)
    sim = Simulator(connect(tmp_path / "sim.db"), clock, seed=3)
    first = sim.trigger_incident("config_regression", "M1")
    sim.execute_action(first.id, "ESCALATE_HUMAN")
    second = sim.trigger_incident("sensor_drift", "M2")
    assert (first.id, second.id) == ("INC-001", "INC-002")
    reopened = connect(tmp_path / "sim.db")
    assert reopened.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 2
    assert reopened.execute("SELECT COUNT(*) FROM actions_log").fetchone()[0] == 1


def test_reset_clears_incidents(world: World):
    world.sim.trigger_incident("config_regression", "M1")
    world.sim.reset()
    assert world.sim.list_incidents() == []
    assert world.sim.active_incident() is None


def test_error_logs_hide_info_and_future_lines(world: World):
    world.sim.trigger_incident("network_failure", "M1")
    world.advance(120)
    lines = world.adapter.get_error_logs("M1", 60)
    assert lines and all(" INFO " not in line for line in lines)
    now = int(world.clock.now())
    future = world.sim._conn.execute("SELECT COUNT(*) FROM logs WHERE ts > ?", (now,)).fetchone()[0]
    assert future == 0


def test_closing_an_incident_refreshes_the_machine_immediately(world: World):
    """Regression: after an escalation the machine still read degraded until the next tick,
    so an incident triggered right away was rejected (MACHINE_NOT_HEALTHY)."""
    incident = world.sim.trigger_incident("sensor_drift", "M2")
    world.advance(FULLY_DEGRADED_S)
    world.adapter.execute_action(incident.id, "ESCALATE_HUMAN")
    assert world.adapter.get_machine_metrics("M2").status == "healthy"
    assert world.sim.trigger_incident("sensor_drift", "M2").machine_id == "M2"


def test_spec_seed_makes_an_incident_identical_whatever_came_before():
    from backend.simulator import ManualClock, Simulator
    from backend.simulator.db import connect

    def run(history_hours: int):
        clock = ManualClock(1_790_000_000.0)
        conn = connect(":memory:")
        sim = Simulator(conn, clock, seed=1)
        for _ in range(history_hours):  # different pasts consume the random stream differently
            clock.advance(3_600)
            sim.tick()
        inc = sim.trigger_incident("config_regression", "M3", spec_seed=42)
        clock.advance(600)
        sim.tick()
        # The incident relative to its onset: same log wording/values at the same offsets,
        # same throughput trajectory.
        logs = conn.execute(
            "SELECT ts - ?, level, message FROM logs WHERE machine_id = 'M3' AND ts >= ? "
            "ORDER BY ts",
            (inc.onset_ts, inc.onset_ts),
        ).fetchall()
        tp = [p.value for p in sim.get_metric_history("M3", "throughput_pct", 600 / 3600)]
        return inc.affected, logs, tp[-30:]

    a = run(0)
    b = run(5)
    assert a[0] == b[0]
    assert a[1] == b[1] and a[1]  # identical, and not trivially empty
    # Background jitter is a smoothed random process that carries its state from before the
    # incident, so readings may differ in the second decimal — never in shape or magnitude.
    assert max(abs(x - y) for x, y in zip(a[2], b[2], strict=True)) < 0.1
