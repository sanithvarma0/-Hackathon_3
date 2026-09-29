"""Memory interfaces used by the agent. `HindsightMemory` is the only implementation in
production; tests use an in-process fake with the same contract."""

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict


class MemoryMatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    rank: int  # 1 = best
    rerank: float  # best cross-encoder score among this incident's facts
    similarity: float | None  # best semantic cosine (display only; not discriminative, 14.1)
    strength: Literal["strong", "weak"]
    diagnosis: str | None
    final_action: str | None
    outcome: str | None
    facts: tuple[str, ...]
    signature: str | None = None  # the past incident's pattern, to check the fit against
    decisive_evidence: str | None = None
    engineer_note: str | None = None


# How the matches were gated: Hindsight's cross-encoder (normal), an LLM relevance check when
# the cross-encoder is unavailable, or rank order alone when that failed too (memory/judge.py).
Gate = Literal["reranker", "llm_judge", "rank_only"]


class MemoryRecall(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    matches: tuple[MemoryMatch, ...]
    learned_patterns: tuple[str, ...]
    gate: Gate = "reranker"


class MemoryRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    incident_id: str
    kind: Literal["episode", "lesson"]
    text: str
    metadata: dict[str, str]
    timestamp: datetime


class MemoryStore(Protocol):
    async def recall(self, query: str, *, exclude_incident: str | None = None) -> MemoryRecall: ...

    async def runbook(self) -> str | None: ...

    async def observations(self, limit: int = 12) -> list[str]: ...

    async def retain(self, record: MemoryRecord) -> None: ...

    async def check_refresh(self) -> bool:
        """True (and the cost recorded) if the runbook was rebuilt since the last check."""
        ...
