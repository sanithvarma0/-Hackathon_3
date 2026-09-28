"""The agent graph (BUILD_PLAN.md 7.2).

START -> recall_hints -> investigate -> search_memory -> decide -> request_approval -> act
act --ESCALATE_HUMAN--> learn
act --otherwise--> verify
verify --full_recovery--> learn
verify --failed, attempts left--> record_lesson -> investigate   (retry loop)
verify --failed, attempts exhausted--> escalate -> learn
learn -> END
"""

from collections.abc import Awaitable, Callable
from typing import Any, cast

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from backend.agent.deps import AgentDeps
from backend.agent.nodes.act import act, escalate, request_approval, verify
from backend.agent.nodes.decide import decide
from backend.agent.nodes.investigate import investigate
from backend.agent.nodes.learn import learn, record_lesson
from backend.agent.nodes.memory import recall_hints, search_memory
from backend.agent.state import AgentState

Node = Callable[[AgentState, AgentDeps], Awaitable[dict[str, Any]]]


def _bind(node: Node, deps: AgentDeps) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    async def run(state: AgentState) -> dict[str, Any]:
        return await node(state, deps)

    return run


def route_after_act(state: AgentState) -> str:
    return "learn" if state["chosen_action"] == "ESCALATE_HUMAN" else "verify"


def route_after_verify(state: AgentState, max_attempts: int) -> str:
    if state["outcome"]["effect"] == "full_recovery":
        return "learn"
    if state.get("attempt_count", 0) < max_attempts:
        return "record_lesson"
    return "escalate"


def build_graph(deps: AgentDeps) -> CompiledStateGraph[Any, Any, Any, Any]:
    g: StateGraph[Any, Any, Any, Any] = StateGraph(AgentState)
    nodes: dict[str, Node] = {
        "recall_hints": recall_hints,
        "investigate": investigate,
        "search_memory": search_memory,
        "decide": decide,
        "request_approval": request_approval,
        "act": act,
        "verify": verify,
        "record_lesson": record_lesson,
        "escalate": escalate,
        "learn": learn,
    }
    for name, node in nodes.items():
        g.add_node(name, cast(Any, _bind(node, deps)))
    g.add_edge(START, "recall_hints")
    g.add_edge("recall_hints", "investigate")
    g.add_edge("investigate", "search_memory")
    g.add_edge("search_memory", "decide")
    g.add_edge("decide", "request_approval")
    g.add_edge("request_approval", "act")
    g.add_conditional_edges("act", route_after_act, ["learn", "verify"])
    g.add_conditional_edges(
        "verify",
        lambda s: route_after_verify(s, deps.max_attempts),
        ["learn", "record_lesson", "escalate"],
    )
    g.add_edge("record_lesson", "investigate")
    g.add_edge("escalate", "learn")
    g.add_edge("learn", END)
    return g.compile(checkpointer=InMemorySaver())
