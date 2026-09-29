"""The four tools the LLM sees (BUILD_PLAN.md 7.3): thin, validated wrappers over the adapter.

Arguments are validated with Pydantic before anything executes (measured: a model silently
rewrote an invalid machine ID rather than failing). Results are rendered as compact text with
relative times, which keeps prompts small and easy for the model to reason over.
"""

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend.adapters import AdapterError, EnvironmentAdapter
from backend.schemas import Metric
from backend.simulator.machines import CALIBRATION_INTERVAL_DAYS, MACHINES


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")

    machine_id: str = Field(description="Machine ID, one of M1, M2, M3, M4, M5")

    @field_validator("machine_id")
    @classmethod
    def _known_machine(cls, v: str) -> str:
        if v not in MACHINES:
            raise ValueError(f"unknown machine {v!r}; valid: {', '.join(MACHINES)}")
        return v


class MachineArgs(_Args):
    pass


class HistoryArgs(_Args):
    metric: Metric = Field(description="Which metric to fetch")
    window_hours: float = Field(gt=0, le=168, description="How far back to look, in hours")


class WindowArgs(_Args):
    window_minutes: int = Field(gt=0, le=1440, description="How far back to look, in minutes")


TOOL_DOCS: dict[str, tuple[type[_Args], str]] = {
    "get_machine_metrics": (
        MachineArgs,
        "Current snapshot of one machine: status, throughput, OEE, error rate, temperature, "
        "sensor variance, packet loss, latency, controller memory, config version and sensor "
        "calibration age. Use first. Also use on other machines to check whether a problem is "
        "local or shared (e.g. several machines behind the same network gateway).",
    ),
    "get_metric_history": (
        HistoryArgs,
        "Time series of one metric for one machine (at most 60 points). Use to tell a gradual "
        "decline from a sudden drop, and to spot slow multi-day trends such as memory climbing.",
    ),
    "get_recent_events": (
        WindowArgs,
        "Change and maintenance events for one machine and its network gateway: config deploys "
        "and rollbacks, sensor calibrations, restarts, out-of-memory kills, gateway events. Use "
        "to find what changed shortly before the problem started.",
    ),
    "get_error_logs": (
        WindowArgs,
        "Controller log lines at WARN level and above for one machine. Use to see the error "
        "pattern (timeouts, alarms, network errors, memory errors).",
    ),
}


# ---- memory during the investigation (offered only when memory is ON) ---------------------

MEMORY_TOOL = "recall_similar_incidents"


class RecallArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observations: str = Field(
        min_length=15,
        max_length=800,
        description="What you have observed so far, stated generally (symptoms, onset, what "
        "changed, error pattern) — no machine IDs or numbers needed",
    )


def memory_tool_spec() -> dict[str, Any]:
    schema = RecallArgs.model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    return {
        "type": "function",
        "function": {
            "name": MEMORY_TOOL,
            "description": "Search this plant's incident memory for past incidents with the "
            "same signature. Returns how each was diagnosed, the evidence that identified it, "
            "and which fixes worked or failed. Use once you know the basic symptoms.",
            "parameters": schema,
        },
    }


def render_recall(matches: list[dict[str, Any]], learned_patterns: list[str]) -> str:
    """What the investigation sees from memory: compact, and explicit that it must be verified."""
    if not matches and not learned_patterns:
        return "No similar past incident in memory. Continue the investigation from evidence."
    lines = ["Similar past incidents at this plant (best first; verify before trusting):"]
    for m in matches:
        lines.append(
            f"- {m['incident_id']} ({m['strength']} match): diagnosis={m.get('diagnosis') or '?'}; "
            f"final fix={m.get('final_action') or '?'} ({m.get('outcome') or '?'})"
        )
        for label, key in (
            ("signature", "signature"),
            ("identified by", "decisive_evidence"),
            ("engineer", "engineer_note"),
        ):
            if m.get(key):
                lines.append(f"    {label}: {m[key]}")
        lines.extend(f"    {f}" for f in m.get("facts", [])[:4])
    if learned_patterns:
        lines.append("Learned patterns:")
        lines.extend(f"- {p}" for p in learned_patterns[:3])
    lines.append(
        "If a match fits, confirm its decisive evidence here with one or two targeted calls and "
        "conclude as soon as it holds. If it does not hold, ignore the match."
    )
    return "\n".join(lines)


def parse_recall_args(raw_args: str | None) -> RecallArgs | str:
    """The validated arguments, or an error message for the model."""
    try:
        return RecallArgs.model_validate(json.loads(raw_args or "{}"))
    except (json.JSONDecodeError, ValidationError) as e:
        return f"ERROR: invalid arguments for {MEMORY_TOOL} ({e}). Expected: observations (text)"


def tool_specs() -> list[dict[str, Any]]:
    """OpenAI-format tool definitions, generated from the argument models."""
    specs = []
    for name, (model, description) in TOOL_DOCS.items():
        schema = model.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        specs.append(
            {
                "type": "function",
                "function": {"name": name, "description": description, "parameters": schema},
            }
        )
    return specs


SERIES_POINTS = 20  # points shown in history text: keeps prompts small (Groq free tier: 8k TPM)
MAX_LOG_LINES = 20


class ToolResult(BaseModel):
    name: str
    args: dict[str, Any]
    ok: bool
    text: str


def _calibration_note(age_days: float) -> str:
    """What a maintenance system shows next to calibration age: the schedule and status."""
    overdue = age_days - CALIBRATION_INTERVAL_DAYS
    if overdue > 0:
        return f"due every {CALIBRATION_INTERVAL_DAYS}d, OVERDUE by {overdue:.0f}d"
    return f"due every {CALIBRATION_INTERVAL_DAYS}d, next due in {-overdue:.0f}d"


def _ago(ts: int, now: int) -> str:
    minutes = (now - ts) / 60
    if minutes < 90:
        return f"{minutes:.0f} min ago"
    if minutes < 48 * 60:
        return f"{minutes / 60:.1f} h ago"
    return f"{minutes / 1440:.1f} days ago"


def _clock(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%H:%M")


def run_tool(adapter: EnvironmentAdapter, name: str, raw_args: str) -> ToolResult:
    """Validate and execute one tool call. Never raises: failures become readable results."""
    if name not in TOOL_DOCS:
        return ToolResult(
            name=name,
            args={},
            ok=False,
            text=f"ERROR: unknown tool {name!r}. Available tools: {', '.join(TOOL_DOCS)}",
        )
    model, _ = TOOL_DOCS[name]
    try:
        parsed = json.loads(raw_args or "{}")
        args = model.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as e:
        detail = (
            e.msg
            if isinstance(e, json.JSONDecodeError)
            else "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        )
        return ToolResult(
            name=name,
            args={},
            ok=False,
            text=f"ERROR: invalid arguments for {name} ({detail}). "
            f"Expected: {json.dumps(model.model_json_schema()['properties'])}",
        )
    try:
        text = _execute(adapter, name, args)
    except AdapterError as e:
        return ToolResult(name=name, args=args.model_dump(), ok=False, text=f"ERROR: {e.message}")
    return ToolResult(name=name, args=args.model_dump(), ok=True, text=text)


def _execute(adapter: EnvironmentAdapter, name: str, args: _Args) -> str:
    mid = args.machine_id
    if isinstance(args, MachineArgs):
        m = adapter.get_machine_metrics(mid)
        return (
            f"{m.machine_id} {m.name} ({m.profile}), gateway {m.gateway}, at {_clock(m.ts)}: "
            f"status={m.status} throughput={m.throughput_pct:.1f}% oee={m.oee_pct:.1f}% "
            f"error_rate={m.error_rate_pct:.2f}% temperature={m.temperature_c:.1f}C "
            f"sensor_variance={m.sensor_variance:.3f} packet_loss={m.packet_loss_pct:.2f}% "
            f"latency={m.latency_ms:.1f}ms memory={m.memory_pct:.1f}% "
            f"config={m.config_version} calibration_age={m.calibration_age_days:.1f}d "
            f"({_calibration_note(m.calibration_age_days)})"
        )
    now = adapter.get_machine_metrics(mid).ts
    if isinstance(args, HistoryArgs):
        points = adapter.get_metric_history(mid, args.metric, args.window_hours)
        if not points:
            return f"no {args.metric} data for {mid} in the last {args.window_hours} h"
        values = [p.value for p in points]
        step_min = (points[-1].ts - points[0].ts) / 60 / max(1, len(points) - 1)
        shown = points[:: max(1, len(points) // SERIES_POINTS)][-SERIES_POINTS:]
        if shown[-1] is not points[-1]:
            shown.append(points[-1])
        series = ", ".join(f"{_ago(p.ts, now)}: {p.value:g}" for p in shown)
        return (
            f"{args.metric} for {mid}, last {args.window_hours:g} h, one point per "
            f"~{step_min:.0f} min: first={values[0]:g} min={min(values):g} max={max(values):g} "
            f"last={values[-1]:g}. For onset shape use a window of 1-2 h.\n{series}"
        )
    assert isinstance(args, WindowArgs)
    if name == "get_recent_events":
        events = adapter.get_recent_events(mid, args.window_minutes)
        if not events:
            return f"no events for {mid} or its gateway in the last {args.window_minutes} min"
        return "\n".join(
            f"{_clock(e.ts)} ({_ago(e.ts, now)}) {e.machine_id} {e.event_type}: {e.detail}"
            for e in events
        )
    lines = adapter.get_error_logs(mid, args.window_minutes)[-MAX_LOG_LINES:]
    if not lines:
        return f"no WARN/ERROR/ALARM log lines for {mid} in the last {args.window_minutes} min"
    return f"now is {_clock(now)} UTC\n" + "\n".join(lines)
