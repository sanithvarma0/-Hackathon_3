"""The EnvironmentAdapter contract: typed, validated, and free of ground truth."""

import pytest

from backend.adapters import AdapterError, EnvironmentAdapter, SimulatorAdapter
from backend.adapters.datadog import DatadogAdapter
from backend.schemas import ActionReceipt, MachineMetrics
from tests.conftest import World


def test_simulator_adapter_satisfies_protocol(world: World):
    assert isinstance(world.adapter, EnvironmentAdapter)


def test_datadog_stub_satisfies_protocol_and_refuses():
    adapter = DatadogAdapter()
    assert isinstance(adapter, EnvironmentAdapter)
    with pytest.raises(NotImplementedError):
        adapter.get_machine_metrics("M1")
    with pytest.raises(NotImplementedError):
        adapter.execute_action("INC-001", "ROLLBACK_CONFIG")


def test_no_ground_truth_crosses_the_boundary():
    """The agent must discover the incident class and the effect of a fix itself."""
    assert "type" not in MachineMetrics.model_fields
    assert "effect" not in ActionReceipt.model_fields
    assert "recovery_pct" not in ActionReceipt.model_fields


def test_receipt_confirms_execution_only(world: World):
    incident = world.sim.trigger_incident("config_regression", "M1")
    world.advance(300)
    receipt = world.adapter.execute_action(incident.id, "RESTART_MACHINE")
    assert receipt.action == "RESTART_MACHINE"
    assert receipt.message == "M1 controller restarted"


@pytest.mark.parametrize(
    ("call", "code"),
    [
        (lambda a: a.get_machine_metrics("M9"), "UNKNOWN_MACHINE"),
        (lambda a: a.get_recent_events("M1", 0), "INVALID_WINDOW"),
        (lambda a: a.get_error_logs("M1", 10_000), "INVALID_WINDOW"),
        (lambda a: a.get_metric_history("M1", "throughput_pct", -1), "INVALID_WINDOW"),
        (lambda a: a.execute_action("INC-404", "ROLLBACK_CONFIG"), "INCIDENT_NOT_FOUND"),
        (lambda a: a.observe_recovery("INC-404", 0), "INVALID_WINDOW"),
    ],
)
def test_errors_are_translated(world: World, call, code):
    with pytest.raises(AdapterError) as e:
        call(world.adapter)
    assert e.value.code == code


def test_executed_by_is_recorded(world: World):
    adapter = SimulatorAdapter(world.sim, executed_by="judge")
    incident = world.sim.trigger_incident("config_regression", "M1")
    adapter.execute_action(incident.id, "ROLLBACK_CONFIG")
    assert world.sim.action_log(incident.id)[0].executed_by == "judge"
