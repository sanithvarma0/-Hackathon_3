"""investigate: LLM tool-calling loop, capped at MAX_TOOL_CALLS (guardrail 2)."""

from typing import Any

from backend.agent import prompts
from backend.agent.deps import AgentDeps
from backend.agent.models import InvestigationSummary
from backend.agent.state import AgentState
from backend.agent.tools import (
    MEMORY_TOOL,
    ToolResult,
    memory_tool_spec,
    parse_recall_args,
    render_recall,
    run_tool,
    tool_specs,
)
from backend.guardrails import GuardrailViolation, ToolBudget, parse_model
from backend.llm import LLMResponse, LLMUnavailable
from backend.memory.render import paraphrase
from backend.schemas import Alert

TOOLS = tool_specs()
TOOLS_WITH_MEMORY = [*TOOLS, memory_tool_spec()]


def _assistant_message(resp: LLMResponse, ids: list[str]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": resp.content or "",
        "tool_calls": [
            {
                "id": ids[i],
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for i, tc in enumerate(resp.tool_calls)
        ],
    }


async def investigate(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    incident_id = state["incident_id"]
    attempt = state.get("attempt_count", 0) + 1
    alert = Alert.model_validate(state["alert"])
    deps.emit("investigation_start", incident_id, {"attempt": attempt})
    # Memory OFF: no memory tool and no mention of it, so nothing can leak in (6.4).
    memory_on = bool(state["memory_enabled"]) and deps.memory is not None
    tools = TOOLS_WITH_MEMORY if memory_on else TOOLS
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": prompts.INVESTIGATE_SYSTEM.format(
                max_tool_calls=deps.max_tool_calls,
                memory_block=prompts.INVESTIGATE_MEMORY_BLOCK if memory_on else "",
            ),
        },
        {
            "role": "user",
            "content": prompts.investigate_user(
                alert, state.get("hints", []), state.get("runbook"), state.get("attempts", [])
            ),
        },
    ]
    budget = ToolBudget(deps.max_tool_calls)
    steps: list[dict[str, Any]] = []
    llm_errors = list(state.get("llm_errors", []))
    final: LLMResponse | None = None

    with deps.tracer.span(f"investigate (attempt {attempt})", as_type="chain") as span:
        try:
            while True:
                if budget.exhausted():
                    messages.append(
                        {
                            "role": "user",
                            "content": "Tool budget exhausted. Conclude now with the JSON object.",
                        }
                    )
                    final = await deps.llm.chat(messages, json_mode=True, name="investigate:force")
                    break
                resp = await deps.llm.chat(messages, tools=tools, name="investigate")
                if not resp.tool_calls:
                    final = resp
                    break
                ids = [f"call_{attempt}_{len(steps) + i + 1}" for i in range(len(resp.tool_calls))]
                messages.append(_assistant_message(resp, ids))
                for call_id, tc in zip(ids, resp.tool_calls, strict=True):
                    if budget.exhausted():
                        content = "Not executed: tool budget exhausted."
                    else:
                        budget.spend()
                        step_no = len(steps) + 1
                        deps.emit(
                            "tool_call",
                            incident_id,
                            {"step": step_no, "tool_name": tc.name, "args": tc.arguments},
                        )
                        with deps.tracer.span(tc.name, as_type="tool", input=tc.arguments) as t:
                            if memory_on and tc.name == MEMORY_TOOL:
                                result = await _recall(deps, incident_id, tc.arguments)
                            else:
                                result = run_tool(deps.adapter, tc.name, tc.arguments)
                            t.update(output=result.text)
                        call = f"{tc.name}({', '.join(f'{v}' for v in result.args.values())})"
                        steps.append(
                            {
                                "step": step_no,
                                "attempt": attempt,
                                "tool": tc.name,
                                "call": call,
                                "args": result.args,
                                "ok": result.ok,
                                "result": result.text,
                            }
                        )
                        deps.emit(
                            "tool_result",
                            incident_id,
                            {
                                "step": step_no,
                                "tool_name": tc.name,
                                "ok": result.ok,
                                "result": result.text,
                            },
                        )
                        content = (
                            f"[step {step_no}] {result.text}\n({budget.remaining} tool calls "
                            f"left; conclude with the JSON as soon as the cause is clear)"
                        )
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
            summary = await _parse_summary(deps, messages, final)
        except LLMUnavailable as e:
            llm_errors.append(str(e))
            deps.emit("error", incident_id, {"code": e.code, "message": str(e)})
            summary = InvestigationSummary(summary=f"Investigation incomplete: {e}", complete=False)
        span.update(output=summary.model_dump())

    deps.emit(
        "investigation_summary",
        incident_id,
        {"attempt": attempt, "summary": summary.model_dump(), "tool_call_count": len(steps)},
    )
    return {
        "steps": steps,
        "all_steps": [*state.get("all_steps", []), *steps],
        "summary": summary.model_dump(),
        "llm_errors": llm_errors,
    }


async def _parse_summary(
    deps: AgentDeps, messages: list[dict[str, Any]], final: LLMResponse | None
) -> InvestigationSummary:
    """Guardrail 1: validate; one corrective JSON-mode retry; then keep the prose as summary."""
    text = final.content if final else None
    try:
        return parse_model(InvestigationSummary, text)
    except GuardrailViolation as violation:
        messages.append({"role": "assistant", "content": text or ""})
        messages.append(
            {
                "role": "user",
                "content": f"Your reply was invalid: {violation}. Reply with ONLY the JSON object "
                f"described in the instructions.",
            }
        )
        retry = await deps.llm.chat(messages, json_mode=True, name="investigate:correct")
        try:
            return parse_model(InvestigationSummary, retry.content)
        except GuardrailViolation:
            return InvestigationSummary(summary=(text or retry.content or "")[:1000])


async def _recall(deps: AgentDeps, incident_id: str, raw_args: str | None) -> ToolResult:
    """The memory tool: same match gate as search_memory, results framed as hypotheses."""
    assert deps.memory is not None
    args = parse_recall_args(raw_args)
    if isinstance(args, str):
        return ToolResult(name=MEMORY_TOOL, args={}, ok=False, text=args)
    query = paraphrase(args.observations) or args.observations
    deps.emit("memory_search", incident_id, {"query": query, "stage": "investigate"})
    try:
        recall = await deps.memory.recall(query, exclude_incident=incident_id)
    except Exception as e:  # memory down: the investigation carries on from evidence
        deps.emit("error", incident_id, {"code": "MEMORY_UNAVAILABLE", "message": str(e)})
        return ToolResult(
            name=MEMORY_TOOL,
            args={"observations": args.observations},
            ok=False,
            text="Memory is unavailable right now. Continue from evidence.",
        )
    matches = [m.model_dump() for m in recall.matches]
    deps.emit(
        "memory_results",
        incident_id,
        {
            "query": query,
            "matches": matches,
            "learned_patterns": list(recall.learned_patterns),
            "stage": "investigate",
        },
    )
    return ToolResult(
        name=MEMORY_TOOL,
        args={"observations": args.observations},
        ok=True,
        text=render_recall(matches, list(recall.learned_patterns)),
    )
