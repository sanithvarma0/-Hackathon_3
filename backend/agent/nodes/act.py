"""request_approval, act, verify, escalate: no LLM in any of these."""

from typing import Any

from langgraph.types import interrupt

from backend.adapters import AdapterError
from backend.agent.deps import AgentDeps
from backend.agent.state import AgentState
from backend.schemas import ACTIONS, Action


async def request_approval(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    """Show the recommendation to a human. Human wait starts here (excluded from MTTR)."""
    incident_id = state["incident_id"]
    if state.get("approval", "human") == "human":
        deps.lifecycle.acknowledge(incident_id)
    deps.emit(
        "awaiting_action",
        incident_id,
        {"recommended": state["recommendation"]["action"], "options": list(ACTIONS)},
    )
    return {}


async def act(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    """Pause for the human (LangGraph interrupt), then execute through the adapter."""
    incident_id = state["incident_id"]
    attempt = state.get("attempt_count", 0) + 1
    recommended = state["recommendation"]["action"]
    forced = state.get("forced_actions", {}).get(str(attempt))
    if forced is not None:
        action = forced
    elif state.get("approval", "human") == "auto":
        action = recommended
    else:
        decision = interrupt(
            {
                "incident_id": incident_id,
                "attempt": attempt,
                "recommendation": state["recommendation"],
            }
        )
        action = decision["action"]
    if action not in ACTIONS:
        raise ValueError(f"{action!r} is not a whitelisted action")
    chosen: Action = action
    with deps.tracer.span("execute_action", as_type="tool", input=chosen) as span:
        receipt = deps.adapter.execute_action(incident_id, chosen)
        span.update(output=receipt.message)
    deps.emit(
        "action_executed",
        incident_id,
        {
            "attempt": attempt,
            "action": chosen,
            "recommended": recommended,
            "message": receipt.message,
        },
    )
    return {"chosen_action": chosen, "attempt_count": attempt}


def classify(held: bool, recovered: bool) -> str:
    """The agent infers the effect from observation; it is never told (BUILD_PLAN 5.8)."""
    if held:
        return "full_recovery"
    return "partial_recovery" if recovered else "no_effect"


async def verify(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    incident_id = state["incident_id"]
    window = deps.verify_window_sim_s
    deps.emit("verifying", incident_id, {"window_sim_s": window})
    await deps.wait_sim(window)
    obs = deps.adapter.observe_recovery(incident_id, window)
    while not obs.complete:  # the clock can lag a little behind a live sleep
        await deps.wait_sim(window - obs.elapsed_sim_s)
        obs = deps.adapter.observe_recovery(incident_id, window)
    effect = classify(obs.held, obs.recovered)
    attempt = {
        "action": state["chosen_action"],
        "effect": effect,
        "peak_throughput_pct": obs.peak_throughput_pct,
        "min_throughput_pct": obs.min_throughput_pct,
    }
    deps.emit("verify_result", incident_id, attempt)
    return {"outcome": attempt, "attempts": [*state.get("attempts", []), attempt]}


async def escalate(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    """Attempts exhausted: hand over to a human rather than keep guessing."""
    incident_id = state["incident_id"]
    try:
        receipt = deps.adapter.execute_action(incident_id, "ESCALATE_HUMAN")
        message = receipt.message
    except AdapterError as e:
        message = e.message
    deps.emit(
        "action_executed",
        incident_id,
        {
            "attempt": state.get("attempt_count", 0) + 1,
            "action": "ESCALATE_HUMAN",
            "recommended": "ESCALATE_HUMAN",
            "message": message,
        },
    )
    return {"chosen_action": "ESCALATE_HUMAN"}
