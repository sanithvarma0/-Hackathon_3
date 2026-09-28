"""decide: one validated Recommendation (guardrails 1 + 3); escalation if the LLM can't."""

from typing import Any

from backend.agent import prompts
from backend.agent.deps import AgentDeps
from backend.agent.models import Recommendation
from backend.agent.state import AgentState
from backend.guardrails import GuardrailViolation, validate_recommendation
from backend.llm import LLMUnavailable
from backend.schemas import Alert


def _escalation(reason: str, signature: str) -> Recommendation:
    return Recommendation(
        action="ESCALATE_HUMAN",
        diagnosis="unresolved",
        signature=signature or "incident whose cause could not be determined automatically",
        confidence=0.0,
        reasoning=reason,
    )


async def decide(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    incident_id = state["incident_id"]
    alert = Alert.model_validate(state["alert"])
    matches = state.get("memory_results", [])
    matched_ids = {m["incident_id"] for m in matches}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": prompts.DECIDE_SYSTEM},
        {
            "role": "user",
            "content": prompts.decide_user(
                alert,
                state.get("summary", {}),
                state.get("steps", []),
                prompts.memory_text(
                    state["memory_enabled"], matches, state.get("learned_patterns", [])
                ),
                state.get("attempts", []),
            ),
        },
    ]
    llm_errors = list(state.get("llm_errors", []))
    if not state.get("summary", {}).get("complete", True):
        # Never let memory alone decide: without evidence, a past incident can't be checked
        # for fit (measured live: this is how a false replay happened). Hand to a human.
        rec = _escalation(
            "The investigation could not complete, so no fix can be verified "
            "against evidence; escalated to the on-call engineer.",
            "",
        )
        recommendation = rec.model_dump() | {"calibrated_confidence": rec.confidence}
        deps.emit("recommendation", incident_id, recommendation)
        update: dict[str, Any] = {"recommendation": recommendation, "llm_errors": llm_errors}
        if "first_recommendation" not in state:
            update["first_recommendation"] = recommendation
        return update
    with deps.tracer.span("decide", as_type="chain") as span:
        try:
            resp = await deps.llm.chat(messages, json_mode=True, name="decide")
            try:
                rec = validate_recommendation(resp.content, matched_ids)
            except GuardrailViolation as violation:
                deps.emit("guardrail", incident_id, {"node": "decide", "message": str(violation)})
                messages.append({"role": "assistant", "content": resp.content or ""})
                messages.append(
                    {
                        "role": "user",
                        "content": f"Your reply was invalid: {violation}. Reply with ONLY the "
                        f"corrected JSON object.",
                    }
                )
                retry = await deps.llm.chat(messages, json_mode=True, name="decide:correct")
                rec = validate_recommendation(retry.content, matched_ids)
        except LLMUnavailable as e:
            llm_errors.append(str(e))
            deps.emit("error", incident_id, {"code": e.code, "message": str(e)})
            rec = _escalation(f"LLM unavailable ({e}); escalated to the on-call engineer.", "")
        except GuardrailViolation as e:
            deps.emit("error", incident_id, {"code": "INVALID_RECOMMENDATION", "message": str(e)})
            rec = _escalation(f"No valid recommendation after a corrective retry ({e}).", "")
        failed = {a["action"] for a in state.get("attempts", [])}
        if rec.action in failed:
            rec = _escalation(
                f"Recommended {rec.action}, which already failed on this incident.", rec.signature
            )
        span.update(output=rec.model_dump())

    recommendation = rec.model_dump() | {"calibrated_confidence": rec.confidence}
    deps.emit("recommendation", incident_id, recommendation)
    update = {"recommendation": recommendation, "llm_errors": llm_errors}
    if "first_recommendation" not in state:
        update["first_recommendation"] = recommendation
    return update
