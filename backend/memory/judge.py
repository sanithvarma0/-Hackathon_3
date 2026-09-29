"""Relevance check for memory candidates when Hindsight's cross-encoder is unavailable.

Hindsight scores recall results with a cross-encoder (`scores.reranker`), which the match gate
(6.3) is built on. When the deployment runs a passthrough reranker (RRF / interleave modes —
observed on Hindsight Cloud on 2026-09-29), `reranker` is null and `final` only encodes rank
position, so there is nothing to gate on. Rather than silently returning no matches (or every
candidate), the memory layer asks the agent's LLM to rate each top-ranked candidate against the
observed evidence — the same question a cross-encoder answers — and records that it did so.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from backend.llm import LLMClient

Judge = Callable[[str, list[dict[str, Any]]], Awaitable[dict[str, float]]]

JUDGE_SYSTEM = """\
You check whether past factory incidents are the same kind of incident as a new one.
For each candidate, give the probability (0 to 1) that it has the same signature as the new
incident. Judge by the TRIGGER and the IDENTIFYING EVIDENCE (what changed before the onset, what
the investigation or the engineer found), not by symptoms: different incident classes share
symptoms (throughput loss, motion or servo errors, timeouts). If the candidate was identified by
evidence the new incident does not show, or the new incident shows a trigger the candidate did
not have (for example a config deploy just before the onset versus none), score it low.
Generic overlap such as "output dropped" or the same machine is not enough.
Reply with ONLY this JSON object: {"scores": {"<incident id>": <0..1>, ...}}"""


def llm_judge(llm: LLMClient) -> Judge:
    async def judge(query: str, candidates: list[dict[str, Any]]) -> dict[str, float]:
        lines = [f"NEW INCIDENT (observed): {query}", "", "CANDIDATES:"]
        for c in candidates:
            md = c.get("md") or {}
            lines.append(f"- {c['incident_id']}:")
            for label, key in (
                ("signature", "signature"),
                ("diagnosis", "diagnosis"),
                ("identified by", "decisive_evidence"),
                ("engineer", "engineer_note"),
            ):
                if md.get(key):
                    lines.append(f"    {label}: {md[key]}")
            lines.extend(f"    fact: {f}" for f in c["facts"][:3])
        resp = await llm.chat(
            [
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": "\n".join(lines)},
            ],
            json_mode=True,
            name="memory_judge",
        )
        raw = json.loads(resp.content or "{}").get("scores", {})
        known = {c["incident_id"] for c in candidates}
        return {
            str(k): min(1.0, max(0.0, float(v)))
            for k, v in raw.items()
            if k in known and isinstance(v, int | float)
        }

    return judge
