"""Hindsight Cloud implementation of `MemoryStore` (BUILD_PLAN.md 6).

Uses the async client exclusively: the sync wrappers bind to whichever event loop the calling
thread has, which breaks under a server that calls from several threads.
"""

from collections import defaultdict
from typing import Any

from hindsight_client import Hindsight

from backend.memory.judge import Judge
from backend.memory.store import Gate, MemoryMatch, MemoryRecall, MemoryRecord
from backend.usage import UsageLedger, estimate_tokens

INCIDENT_PATTERNS_ID = "incident-patterns"

MISSION = (
    "I am a production incident responder for a 5-machine factory. I learn from every incident "
    "resolution to diagnose faster and more accurately."
)
RETAIN_MISSION = (
    "Extract incident symptoms, the evidence that identified the root cause, which remediation "
    "actions worked or failed and why, and time to recovery."
)
OBSERVATIONS_MISSION = (
    "Observations are stable facts about recurring incident classes: their signatures, the fix "
    "that works, fixes that only give temporary relief, and which evidence identifies them "
    "fastest. Ignore one-off noise such as exact percentages, timestamps and machine IDs."
)
RUNBOOK_QUERY = (
    "What incident classes recur on this factory floor? For each: its signature, the evidence "
    "that identifies it fastest, the fix that works, and fixes that only give temporary relief "
    "or no effect."
)


def apply_match_rule(
    facts: list[Any], *, rel: float, floor: float, exclude_incident: str | None = None
) -> list[MemoryMatch]:
    """Group raw facts into per-incident matches and gate them (guardrail 6, BUILD_PLAN 6.3).

    A past incident matches when its best reranker score is >= rel x the top score among all
    returned incidents and >= floor. Semantic cosine is kept for display only: measured to be
    ~0.7 for every factory incident, true or false (14.1).
    """
    best_rerank: dict[str, float] = defaultdict(float)
    best_sim: dict[str, float | None] = {}
    first_rank: dict[str, int] = {}
    meta: dict[str, dict[str, str]] = {}
    texts: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for i, f in enumerate(facts):
        md = f.metadata or {}
        inc = md.get("incident_id")
        if not inc or inc == exclude_incident or f.scores is None:
            continue
        rr = float(f.scores.reranker or 0.0)
        best_rerank[inc] = max(best_rerank[inc], rr)
        sem = f.scores.semantic
        prev = best_sim.get(inc)
        best_sim[inc] = sem if prev is None else (prev if sem is None else max(prev, sem))
        first_rank.setdefault(inc, i)
        if md.get("record_kind") == "episode" or inc not in meta:
            meta[inc] = md
        texts[inc].append((rr, f.text))
    if not best_rerank:
        return []
    top = max(best_rerank.values())
    ordered = sorted(best_rerank, key=lambda inc: (-best_rerank[inc], first_rank[inc]))
    matches: list[MemoryMatch] = []
    for inc in ordered:
        rr = best_rerank[inc]
        if rr < floor or rr < rel * top:
            continue
        md = meta[inc]
        sim = best_sim[inc]
        matches.append(
            MemoryMatch(
                incident_id=inc,
                rank=len(matches) + 1,
                rerank=round(rr, 4),
                similarity=None if sim is None else round(float(sim), 4),
                strength="strong" if rr >= 0.5 * top else "weak",
                diagnosis=md.get("diagnosis"),
                final_action=md.get("final_action"),
                outcome=md.get("outcome"),
                facts=tuple(t for _, t in sorted(texts[inc], key=lambda x: -x[0])[:4]),
            )
        )
    return matches


JUDGE_CANDIDATES = 5  # top-ranked past incidents the fallback judge looks at
JUDGE_MIN = 0.5  # fallback gate: judged probability of the same signature
JUDGE_STRONG = 0.75
RANK_ONLY_KEEP = 2  # last resort (no scores, judge failed): the top two, marked weak


def rank_candidates(
    facts: list[Any], *, exclude_incident: str | None = None, limit: int = JUDGE_CANDIDATES
) -> list[dict[str, Any]]:
    """Past incidents in Hindsight's rank order (used when there are no reranker scores)."""
    by_incident: dict[str, dict[str, Any]] = {}
    for f in facts:
        md = f.metadata or {}
        inc = md.get("incident_id")
        if not inc or inc == exclude_incident:
            continue
        c = by_incident.setdefault(inc, {"incident_id": inc, "facts": [], "md": md, "sim": None})
        if md.get("record_kind") == "episode":
            c["md"] = md
        c["facts"].append(f.text)
        sem = getattr(f.scores, "semantic", None) if f.scores is not None else None
        if sem is not None:
            c["sim"] = sem if c["sim"] is None else max(c["sim"], sem)
    return list(by_incident.values())[:limit]


def _match(c: dict[str, Any], rank: int, score: float, strength: str) -> MemoryMatch:
    md = c["md"]
    return MemoryMatch(
        incident_id=c["incident_id"],
        rank=rank,
        rerank=round(score, 4),
        similarity=None if c["sim"] is None else round(float(c["sim"]), 4),
        strength="strong" if strength == "strong" else "weak",
        diagnosis=md.get("diagnosis"),
        final_action=md.get("final_action"),
        outcome=md.get("outcome"),
        facts=tuple(c["facts"][:4]),
    )


class HindsightMemory:
    def __init__(
        self,
        client: Hindsight,
        bank_id: str,
        *,
        rel_rerank: float,
        min_rerank: float,
        ledger: UsageLedger | None = None,
        judge: Judge | None = None,
    ) -> None:
        self._client = client
        self._ledger = ledger
        self._judge = judge
        self.bank_id = bank_id
        self._rel = rel_rerank
        self._floor = min_rerank
        self._last_refresh: str | None = None  # runbook's last_refreshed_at, last seen

    async def ensure_bank(self) -> None:
        """Create the bank (idempotent) and the Incident Patterns mental model (6.1, 6.5)."""
        await self._client.acreate_bank(
            bank_id=self.bank_id,
            name="MemoryOps incident memory",
            mission=MISSION,
            retain_mission=RETAIN_MISSION,
            observations_mission=OBSERVATIONS_MISSION,
        )
        existing = await self._client.alist_mental_models(bank_id=self.bank_id)
        items = getattr(existing, "items", None) or []
        if not any(getattr(m, "id", None) == INCIDENT_PATTERNS_ID for m in items):
            await self._client.acreate_mental_model(
                bank_id=self.bank_id,
                id=INCIDENT_PATTERNS_ID,
                name="Incident Patterns",
                source_query=RUNBOOK_QUERY,
                max_tokens=4096,
                trigger={
                    "refresh_after_consolidation": True,
                    "mode": "delta",
                    "exclude_mental_models": True,
                    "fact_types": ["observation"],
                },
            )
        await self.check_refresh()  # baseline: creating the model is not a billed refresh

    async def check_refresh(self) -> bool:
        """Record a runbook refresh (billed per call) if the model was rebuilt since last seen.

        Hindsight refreshes the mental model itself after consolidation, so refreshes are
        observed through `last_refreshed_at` (metadata only, no content). Several refreshes
        between two checks count once: a lower bound.
        """
        try:
            model = await self._client.aget_mental_model(
                bank_id=self.bank_id, mental_model_id=INCIDENT_PATTERNS_ID, detail="metadata"
            )
        except Exception:
            return False
        seen = str(getattr(model, "last_refreshed_at", "") or "")
        if self._last_refresh is None or not seen:
            self._last_refresh = seen or self._last_refresh
            return False
        if seen == self._last_refresh:
            return False
        self._last_refresh = seen
        if self._ledger is not None:
            self._ledger.record_memory(op="mm_refresh", calls=1)
        return True

    async def delete_bank(self) -> None:
        await self._client.adelete_bank(self.bank_id)

    async def recall(self, query: str, *, exclude_incident: str | None = None) -> MemoryRecall:
        resp = await self._client.arecall(
            bank_id=self.bank_id,
            query=query,
            types=["world", "experience", "observation"],
            budget="mid",
            max_tokens=4096,
        )
        if self._ledger is not None:  # billed on the text returned (estimate)
            self._ledger.record_memory(
                op="recall", billed_tokens=sum(estimate_tokens(r.text) for r in resp.results)
            )
        facts = [r for r in resp.results if r.type in ("world", "experience")]
        observations = tuple(r.text for r in resp.results if r.type == "observation")[:5]
        scored = any(f.scores is not None and f.scores.reranker is not None for f in facts)
        gate: Gate = "reranker"
        if scored or not facts:
            matches = apply_match_rule(
                facts, rel=self._rel, floor=self._floor, exclude_incident=exclude_incident
            )
        else:
            matches, gate = await self._fallback_gate(query, facts, exclude_incident)
        return MemoryRecall(
            query=query, matches=tuple(matches), learned_patterns=observations, gate=gate
        )

    async def _fallback_gate(
        self, query: str, facts: list[Any], exclude_incident: str | None
    ) -> tuple[list[MemoryMatch], Gate]:
        """No cross-encoder scores (passthrough reranker): judge the top-ranked candidates."""
        candidates = rank_candidates(facts, exclude_incident=exclude_incident)
        if not candidates:
            return [], "llm_judge"
        if self._judge is not None:
            try:
                scores = await self._judge(query, candidates)
            except Exception:  # the judge is best effort; fall through to rank order
                scores = None
            if scores is not None:
                kept = sorted(
                    (c for c in candidates if scores.get(c["incident_id"], 0.0) >= JUDGE_MIN),
                    key=lambda c: -scores[c["incident_id"]],
                )
                return [
                    _match(
                        c,
                        i + 1,
                        scores[c["incident_id"]],
                        "strong" if scores[c["incident_id"]] >= JUDGE_STRONG else "weak",
                    )
                    for i, c in enumerate(kept)
                ], "llm_judge"
        return [
            _match(c, i + 1, 0.0, "weak") for i, c in enumerate(candidates[:RANK_ONLY_KEEP])
        ], "rank_only"

    async def runbook(self) -> str | None:
        try:
            model = await self._client.aget_mental_model(
                bank_id=self.bank_id, mental_model_id=INCIDENT_PATTERNS_ID, detail="content"
            )
        except Exception:  # missing or not yet built: the runbook is optional context
            return None
        content = getattr(model, "content", None)
        if self._ledger is not None and content:
            self._ledger.record_memory(op="mm_retrieve", billed_tokens=estimate_tokens(content))
        return content or None

    async def retain(self, record: MemoryRecord) -> None:
        resp = await self._client.aretain(
            bank_id=self.bank_id,
            content=record.text,
            context="production incident resolution",
            timestamp=record.timestamp,
            document_id=record.document_id,
            metadata=record.metadata,
            tags=[f"kind:{record.kind}"],
            retain_async=False,  # recallable immediately (measured, 14.1)
        )
        usage = getattr(resp, "usage", None)
        if self._ledger is not None:
            self._ledger.record_memory(
                op=f"retain_{record.kind}",
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
                billed_tokens=estimate_tokens(record.text),  # billed on content (estimate)
            )
