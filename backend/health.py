"""Dependency health checks for /api/health.

Each check reports whether the dependency is configured (key present) and reachable
(an authenticated request succeeds). Checks never raise: failures are reported as data.
"""

import asyncio
from typing import Literal

import httpx
from pydantic import BaseModel

from backend.config import Settings

TIMEOUT_S = 5.0

Status = Literal["ok", "not_configured", "unreachable", "auth_failed", "error"]


class DependencyHealth(BaseModel):
    name: str
    status: Status
    detail: str = ""


class HealthReport(BaseModel):
    ok: bool
    dependencies: list[DependencyHealth]


async def _probe(
    client: httpx.AsyncClient,
    name: str,
    url: str,
    headers: dict[str, str] | None = None,
    auth: tuple[str, str] | None = None,
) -> DependencyHealth:
    try:
        resp = await client.get(url, headers=headers, auth=auth)
    except httpx.HTTPError as e:
        return DependencyHealth(name=name, status="unreachable", detail=f"{type(e).__name__}: {e}")
    if resp.status_code in (401, 403):
        return DependencyHealth(name=name, status="auth_failed", detail=f"HTTP {resp.status_code}")
    if resp.status_code >= 400:
        return DependencyHealth(name=name, status="error", detail=f"HTTP {resp.status_code}")
    return DependencyHealth(name=name, status="ok")


async def check_hindsight(client: httpx.AsyncClient, s: Settings) -> DependencyHealth:
    if not s.hindsight_api_key:
        return DependencyHealth(name="hindsight", status="not_configured")
    # Listing banks requires a valid key, so this checks reachability and auth together.
    return await _probe(
        client,
        "hindsight",
        f"{s.hindsight_base_url.rstrip('/')}/v1/default/banks",
        headers={"Authorization": f"Bearer {s.hindsight_api_key}"},
    )


async def check_openai(client: httpx.AsyncClient, s: Settings) -> DependencyHealth:
    if not s.openai_api_key:
        return DependencyHealth(name="openai", status="not_configured")
    return await _probe(
        client,
        "openai",
        f"{s.openai_base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {s.openai_api_key}"},
    )


async def check_groq(client: httpx.AsyncClient, s: Settings) -> DependencyHealth:
    if not s.groq_api_key:
        return DependencyHealth(name="groq", status="not_configured")
    return await _probe(
        client,
        "groq",
        f"{s.groq_base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {s.groq_api_key}"},
    )


async def check_langfuse(client: httpx.AsyncClient, s: Settings) -> DependencyHealth:
    if not (s.langfuse_public_key and s.langfuse_secret_key):
        return DependencyHealth(name="langfuse", status="not_configured")
    # /api/public/projects requires basic auth with the project key pair.
    return await _probe(
        client,
        "langfuse",
        f"{s.langfuse_host.rstrip('/')}/api/public/projects",
        auth=(s.langfuse_public_key, s.langfuse_secret_key),
    )


async def check_all(s: Settings) -> HealthReport:
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        deps = await asyncio.gather(
            check_hindsight(client, s),
            check_openai(client, s),
            check_groq(client, s),
            check_langfuse(client, s),
        )
    return HealthReport(ok=all(d.status == "ok" for d in deps), dependencies=list(deps))
