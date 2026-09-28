"""Structured outputs the LLM must produce (validated by `backend.guardrails`)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas import Action


class InvestigationSummary(BaseModel):
    """The investigate node's conclusion: evidence only, no recommendation."""

    model_config = ConfigDict(extra="ignore")

    onset: Literal["gradual", "sudden", "unclear"] = "unclear"
    key_signals: list[str] = Field(
        default_factory=list, description="signals actually observed (present, not absent)"
    )
    what_changed: str = ""
    ruled_out: list[str] = Field(default_factory=list)
    decisive_steps: list[int] = Field(
        default_factory=list, description="step numbers of the tool calls that were decisive"
    )
    decisive_evidence: str = ""
    summary: str = ""
    complete: bool = True  # False when the investigation was cut short (e.g. LLM unavailable)


class Recommendation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Action
    diagnosis: str = Field(min_length=3, max_length=80)
    signature: str = Field(
        min_length=10, max_length=300, description="generalized one-liner, no IDs or numbers"
    )
    confidence: float = Field(ge=0, le=1)
    reasoning: str = Field(min_length=10)
    cited_incidents: list[str] = Field(default_factory=list)
    actions_known_to_fail: list[Action] = Field(default_factory=list)
