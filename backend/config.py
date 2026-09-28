"""Application settings, loaded from environment variables and `.env`."""

from functools import lru_cache
from pathlib import Path

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

    # Groq (OpenAI-compatible endpoint)
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    llm_primary_model: str = "openai/gpt-oss-120b"
    llm_fallback_model: str = "qwen/qwen3.8-27b"
    llm_call_budget_s: float = 30.0

    # Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://us.cloud.langfuse.com"

    # Simulator / agent
    sim_speed: float = Field(10.0, gt=0)
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
