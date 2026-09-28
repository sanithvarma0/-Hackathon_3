"""FastAPI entrypoint. Run with: uv run uvicorn backend.main:app --reload"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import get_settings
from backend.health import HealthReport, check_all

settings = get_settings()

app = FastAPI(title="MemoryOps", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", response_model=HealthReport)
async def health() -> HealthReport:
    return await check_all(settings)
