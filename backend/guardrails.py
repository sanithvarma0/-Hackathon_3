"""The six guardrails (BUILD_PLAN.md 8) as reusable checks.

1. Pydantic validation of every LLM output          -> parse_model / validate_recommendation
2. Max tool calls per investigation                 -> ToolBudget
3. Action whitelist                                  -> Recommendation.action is a Literal
4. Dependency retry + fallback                       -> backend.llm / memory writer outbox
5. Simulator state validation                        -> SimulatorError / AdapterError
6. Memory match rule                                 -> backend.memory.hindsight.apply_match_rule
"""

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from backend.agent.models import Recommendation

M = TypeVar("M", bound=BaseModel)

FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class GuardrailViolation(Exception):
    """An LLM output failed validation. The message is written for a corrective re-prompt."""


def extract_json_object(text: str | None) -> dict[str, object]:
    """Pull one JSON object out of a model reply (tolerates code fences and surrounding prose)."""
    if not text:
        raise GuardrailViolation("the reply was empty; reply with a single JSON object")
    cleaned = FENCE_RE.sub("", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise GuardrailViolation("no JSON object found; reply with a single JSON object only")
    try:
        obj = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as e:
        raise GuardrailViolation(f"invalid JSON ({e.msg}); reply with valid JSON only") from e
    if not isinstance(obj, dict):
        raise GuardrailViolation("the JSON must be an object, not a list or value")
    return obj


def parse_model(model: type[M], text: str | None) -> M:
    obj = extract_json_object(text)
    try:
        return model.model_validate(obj)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        raise GuardrailViolation(f"the JSON does not match the schema: {problems}") from e


def validate_recommendation(text: str | None, matched_incidents: set[str]) -> Recommendation:
    """Guardrails 1 + 3, plus: citations must be incidents memory actually returned."""
    rec = parse_model(Recommendation, text)
    unknown = [c for c in rec.cited_incidents if c not in matched_incidents]
    if unknown:
        allowed = ", ".join(sorted(matched_incidents)) or "none (memory returned no matches)"
        raise GuardrailViolation(
            f"cited_incidents contains {', '.join(unknown)}, which memory did not return. "
            f"You may only cite: {allowed}"
        )
    return rec


class ToolBudget:
    """Guardrail 2: hard cap on tool calls per investigation attempt."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def exhausted(self) -> bool:
        return self.used >= self.limit

    def spend(self) -> None:
        self.used += 1
