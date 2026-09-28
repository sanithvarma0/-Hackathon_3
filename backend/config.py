"""Application settings, loaded from environment variables and `.env`."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Hindsight
    hindsight_base_url: str = "https://api.hindsight.vectorize.io"
    hindsight_api_key: str = ""
    hindsight_bank_live: str = "memoryops-live"
    hindsight_bank_seeded: str = "memoryops-seeded"

    # LLM routes: OpenAI primary, Groq fallback (both via the OpenAI SDK)
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    # gpt-5.4-mini on Chat Completions (measured): function tools require reasoning_effort=none,
    # and temperature=0 is only accepted with none. So "none" is the only setting for this agent.
    openai_reasoning_effort: str = "none"
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    llm_primary_provider: Literal["openai", "groq"] = "openai"
    llm_primary_model: str = "gpt-5.4-mini"
    llm_fallback_provider: Literal["openai", "groq"] = "groq"
    llm_fallback_model: str = "openai/gpt-oss-120b"
    # Spend tracking (backend/usage.py): persistent ledger + hard cap on all-time LLM spend
    usage_db_path: Path = REPO_ROOT / "data" / "usage.db"
    eval_dir: Path = REPO_ROOT / "docs" / "eval"  # committed eval reports (latest.json points)
    llm_spend_cap_usd: float = 10.0
    llm_call_budget_s: float = 45.0  # covers Groq rate-limit waits ("try again in 8s")
    llm_seed: int | None = 42  # fixed sampling seed for reproducible runs (eval, BUILD_PLAN 11.2)

    # Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://us.cloud.langfuse.com"

    # Simulator / agent
    sim_speed: float = Field(10.0, gt=0)
    sim_seed: int | None = None  # None: a fresh world each start; set for reproducible demos
    verify_window_sim_s: int = 180
    escalation_penalty_sim_s: int = 1800
    # A past incident counts as a memory match when its best reranker score is at least
    # REL x the top result's reranker score and above MIN (measured in the Phase 0.5 spike:
    # semantic cosine does not separate true from false matches, the reranker does).
    memory_match_rel_rerank: float = Field(0.15, ge=0, le=1)
    memory_match_min_rerank: float = Field(0.05, ge=0, le=1)
    max_tool_calls: int = 10
    max_attempts: int = 3
    sqlite_path: Path = REPO_ROOT / "data" / "memoryops.db"

    # CORS for the Next.js dev server
    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
