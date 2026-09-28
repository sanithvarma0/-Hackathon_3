import json

import pytest

from backend.agent.tools import TOOL_DOCS, run_tool, tool_specs
from backend.guardrails import (
    GuardrailViolation,
    ToolBudget,
    extract_json_object,
    validate_recommendation,
)
from tests.conftest import World
from tests.fakes import decision_json

# ---- guardrails ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ['{"a": 1}', '```json\n{"a": 1}\n```', 'Here is the result:\n{"a": 1}\nThanks.'],
)
def test_json_extraction_tolerates_fences_and_prose(text):
    assert extract_json_object(text) == {"a": 1}


@pytest.mark.parametrize("text", [None, "", "no json here", "[1, 2]", '{"a": }'])
def test_json_extraction_rejects_garbage(text):
    with pytest.raises(GuardrailViolation):
        extract_json_object(text)


def test_recommendation_must_use_a_whitelisted_action():
    with pytest.raises(GuardrailViolation, match="action"):
        validate_recommendation(decision_json("SHUT_DOWN_PLANT"), set())


def test_recommendation_may_only_cite_incidents_memory_returned():
    text = decision_json("ROLLBACK_CONFIG", cited_incidents=["INC-001", "INC-777"])
    with pytest.raises(GuardrailViolation, match="INC-777"):
        validate_recommendation(text, {"INC-001"})
    assert validate_recommendation(text, {"INC-001", "INC-777"}).cited_incidents


def test_confidence_must_be_a_probability():
    with pytest.raises(GuardrailViolation, match="confidence"):
        validate_recommendation(decision_json("ROLLBACK_CONFIG", confidence=1.7), set())


def test_tool_budget():
    budget = ToolBudget(2)
    budget.spend()
    assert not budget.exhausted() and budget.remaining == 1
    budget.spend()
    assert budget.exhausted() and budget.remaining == 0


# ---- tools --------------------------------------------------------------------------------


def test_tool_specs_are_the_four_documented_tools():
    specs = tool_specs()
    assert [s["function"]["name"] for s in specs] == list(TOOL_DOCS)
    history = next(s for s in specs if s["function"]["name"] == "get_metric_history")
    params = history["function"]["parameters"]
    assert set(params["required"]) == {"machine_id", "metric", "window_hours"}
    assert "memory_pct" in params["properties"]["metric"]["enum"]


def test_unknown_tool_and_bad_arguments_are_readable_errors():
    world = World()
    bad_tool = run_tool(world.adapter, "search_incident_history", "{}")
    assert not bad_tool.ok and "Available tools" in bad_tool.text
    bad_json = run_tool(world.adapter, "get_error_logs", "{machine_id: M1")
    assert not bad_json.ok and "invalid arguments" in bad_json.text
    extra = run_tool(
        world.adapter,
        "get_error_logs",
        json.dumps({"machine_id": "M1", "window_minutes": 30, "color": "blue"}),
    )
    assert not extra.ok and "color" in extra.text


def test_tool_results_use_relative_times():
    world = World()
    world.sim.trigger_incident("config_regression", "M2")
    world.advance(600)
    events = run_tool(
        world.adapter, "get_recent_events", json.dumps({"machine_id": "M2", "window_minutes": 60})
    )
    assert events.ok and "min ago" in events.text and "config_deployed" in events.text
    history = run_tool(
        world.adapter,
        "get_metric_history",
        json.dumps({"machine_id": "M2", "metric": "throughput_pct", "window_hours": 1}),
    )
    assert history.ok and "first=" in history.text and "min ago" in history.text
