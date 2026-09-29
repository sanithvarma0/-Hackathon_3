"""The match gate when Hindsight's cross-encoder is unavailable (passthrough reranker).

Observed on Hindsight Cloud on 2026-09-29: `scores.reranker` null for every result and
`final` encoding only rank position — the reranker-based gate then rejected everything and
memory silently stopped matching. These tests pin the degraded paths.
"""

from types import SimpleNamespace
from typing import Any

from backend.memory.hindsight import HindsightMemory, rank_candidates


def fact(incident: str, text: str, reranker: float | None, semantic: float = 0.7) -> Any:
    return SimpleNamespace(
        type="world",
        text=text,
        metadata={"incident_id": incident, "record_kind": "episode", "diagnosis": f"dx {incident}"},
        scores=SimpleNamespace(reranker=reranker, semantic=semantic, final=1.0),
    )


class Client:
    def __init__(self, results: list[Any]) -> None:
        self.results = results

    async def arecall(self, **_: Any) -> Any:
        return SimpleNamespace(results=self.results)


UNSCORED = [
    fact("INC-003", "packet loss on every machine behind the gateway", None),
    fact("INC-001", "config deploy before servo timeouts", None),
    fact("INC-003", "RESTART_GATEWAY resolved it", None),
    fact("INC-002", "phantom temperature alarms", None),
]


def memory(results: list[Any], judge: Any = None) -> HindsightMemory:
    return HindsightMemory(Client(results), "b", rel_rerank=0.15, min_rerank=0.05, judge=judge)  # type: ignore[arg-type]


def test_rank_candidates_keep_hindsight_order_and_group_facts():
    cands = rank_candidates(UNSCORED, exclude_incident="INC-002")
    assert [c["incident_id"] for c in cands] == ["INC-003", "INC-001"]
    assert len(cands[0]["facts"]) == 2


async def test_scored_results_use_the_reranker_gate():
    scored = [fact("INC-003", "packet loss", 0.9), fact("INC-001", "config deploy", 0.01)]
    recall = await memory(scored).recall("q")
    assert recall.gate == "reranker"
    assert [m.incident_id for m in recall.matches] == ["INC-003"]


async def test_unscored_results_are_judged_and_gated():
    seen: list[list[str]] = []

    async def judge(query: str, candidates: list[dict[str, Any]]) -> dict[str, float]:
        seen.append([c["incident_id"] for c in candidates])
        return {"INC-003": 0.9, "INC-001": 0.2, "INC-002": 0.6}

    recall = await memory(UNSCORED, judge).recall("abrupt loss on a gateway")
    assert recall.gate == "llm_judge"
    assert seen == [["INC-003", "INC-001", "INC-002"]]
    assert [(m.incident_id, m.strength) for m in recall.matches] == [
        ("INC-003", "strong"),
        ("INC-002", "weak"),
    ]  # INC-001 judged a different incident: gated out
    assert recall.matches[0].rerank == 0.9 and recall.matches[0].diagnosis == "dx INC-003"


async def test_judge_failure_falls_back_to_rank_order_never_to_silence():
    async def broken(query: str, candidates: list[dict[str, Any]]) -> dict[str, float]:
        raise TimeoutError("llm down")

    recall = await memory(UNSCORED, broken).recall("q")
    assert recall.gate == "rank_only"
    assert [m.incident_id for m in recall.matches] == ["INC-003", "INC-001"]
    assert all(m.strength == "weak" for m in recall.matches)


async def test_no_judge_configured_also_uses_rank_order():
    recall = await memory(UNSCORED).recall("q")
    assert recall.gate == "rank_only" and len(recall.matches) == 2


async def test_empty_memory_is_not_a_degradation():
    recall = await memory([]).recall("q")
    assert recall.gate == "reranker" and recall.matches == ()


async def test_llm_judge_parses_and_clamps_scores():
    from backend.llm import LLMClient, ModelRoute
    from backend.memory.judge import llm_judge
    from tests.fakes import completion

    async def create(**_: Any) -> Any:
        return completion('{"scores": {"INC-003": 1.4, "INC-001": -0.2, "INC-999": 0.9}}')

    judge = llm_judge(LLMClient([ModelRoute("fake", "m", create)]))
    scores = await judge(
        "q",
        [{"incident_id": "INC-003", "facts": ["a"]}, {"incident_id": "INC-001", "facts": ["b"]}],
    )
    assert scores == {"INC-003": 1.0, "INC-001": 0.0}  # clamped; unknown IDs dropped
