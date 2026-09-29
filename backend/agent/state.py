"""LangGraph state (BUILD_PLAN.md 7.1). Plain JSON-serializable values so it checkpoints."""

from typing import Any, Literal, TypedDict


class AgentState(TypedDict, total=False):
    # set at start
    incident_id: str
    memory_enabled: bool
    approval: Literal["auto", "human"]
    forced_actions: dict[str, str]  # attempt number -> action a human will choose (demo/eval)
    alert: dict[str, Any]
    # memory touchpoint #1
    hints: list[str]
    runbook: str | None
    # investigation (current attempt) and all evidence so far
    steps: list[dict[str, Any]]
    all_steps: list[dict[str, Any]]
    summary: dict[str, Any]
    # memory touchpoint #2
    memory_query: str
    memory_results: list[dict[str, Any]]
    learned_patterns: list[str]
    memory_hit: bool
    first_memory_results: list[dict[str, Any]]
    memory_gate: str | None  # how the first memory search was gated (memory/store.py Gate)
    # decision
    recommendation: dict[str, Any]
    first_recommendation: dict[str, Any]
    llm_errors: list[str]
    # action + verification
    chosen_action: str
    attempt_count: int
    attempts: list[dict[str, Any]]
    outcome: dict[str, Any]
    # result
    final: dict[str, Any]
