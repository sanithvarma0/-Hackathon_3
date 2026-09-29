"""FastAPI entrypoint (BUILD_PLAN.md 9). Run: uv run uvicorn backend.main:app --reload

Interactive API docs (OpenAPI) at /docs. Every refusal is a structured JSON error
`{"code", "message"}` and also an `error` event on the stream — never a stack trace.
"""

import hmac
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sse_starlette.sse import EventSourceResponse

from backend.agent.deps import Emit, WaitSim
from backend.config import Settings, get_settings
from backend.health import HealthReport, check_all
from backend.schemas import (
    PREDEFINED_TYPES,
    SITE_KNOWLEDGE_TYPES,
    Action,
    CustomIncidentSpec,
    IncidentType,
    Metric,
)
from backend.service import (
    AgentHandle,
    IncidentView,
    MemoryOpsService,
    ServiceError,
    sim_resume_ts,
)
from backend.simulator import RealtimeClock, Simulator
from backend.simulator.db import connect
from backend.wiring import build_live_agent

ServiceFactory = Callable[[Settings], Awaitable[MemoryOpsService]]


# ---- request / response models --------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PredefinedRequest(_Strict):
    type: IncidentType
    machine: str | None = None
    memory_enabled: bool = True


class RandomRequest(_Strict):
    memory_enabled: bool = True


class CustomRequest(_Strict):
    spec: CustomIncidentSpec
    memory_enabled: bool = True


class ActionRequest(_Strict):
    action: Action


class ResetRequest(_Strict):
    bank: Literal["live", "seeded"] = "live"
    wipe_memory: bool = False


class Accepted(BaseModel):
    incident_id: str
    accepted: bool = True


# ---- live wiring ------------------------------------------------------------------------------


async def live_service(settings: Settings) -> MemoryOpsService:
    """The real system: realtime simulator + live agent (OpenAI/Groq, Hindsight, Langfuse)."""
    conn = connect(settings.sqlite_path)
    resume = sim_resume_ts(conn)
    clock = RealtimeClock(
        speed=settings.sim_speed, start=max(time.time(), resume + 60) if resume else None
    )
    sim = Simulator(
        conn,
        clock,
        seed=settings.sim_seed,
        escalation_penalty_sim_s=settings.escalation_penalty_sim_s,
    )

    async def factory(bank_id: str, emit: Emit, wait_sim: WaitSim, run_label: str) -> AgentHandle:
        live = await build_live_agent(
            settings,
            sim,
            conn,
            bank_id=bank_id,
            emit=emit,
            wait_sim=wait_sim,
            executed_by="judge",
            run_label=run_label,
        )
        return AgentHandle(
            runner=live.runner,
            writer=live.deps.writer,
            memory=live.memory,
            ledger=live.ledger,
            bank_id=bank_id,
            close=live.close,
            delete_bank=live.memory.delete_bank,
        )

    return MemoryOpsService(settings, sim=sim, conn=conn, clock=clock, agent_factory=factory)


def create_app(
    service_factory: ServiceFactory = live_service, settings: Settings | None = None
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service = await service_factory(settings)
        await service.start()
        app.state.service = service
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(
        title="MemoryOps",
        version="0.3.0",
        description="Self-learning production incident commander powered by Hindsight memory.",
        lifespan=lifespan,
    )
    # Registered before CORS so CORS stays outermost: refusals still carry CORS headers and
    # the browser shows the structured error instead of a CORS failure.
    trigger_times: deque[float] = deque()

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Any]]) -> Any:
        if request.method != "POST" or not request.url.path.startswith("/api/"):
            return await call_next(request)
        if settings.demo_passcode:
            given = request.headers.get("x-demo-passcode", "")
            if not hmac.compare_digest(given.encode(), settings.demo_passcode.encode()):
                return JSONResponse(
                    status_code=401,
                    content={"code": "PASSCODE_REQUIRED", "message": "enter the demo passcode"},
                )
        if request.url.path.startswith(
            ("/api/incident/predefined", "/api/incident/random", "/api/incident/custom")
        ):
            now = time.monotonic()
            while trigger_times and now - trigger_times[0] > 3600:
                trigger_times.popleft()
            if len(trigger_times) >= settings.trigger_limit_per_hour:
                return JSONResponse(
                    status_code=429,
                    content={
                        "code": "RATE_LIMITED",
                        "message": f"at most {settings.trigger_limit_per_hour} incidents per hour",
                    },
                )
            trigger_times.append(now)
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, e: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=e.status, content={"code": e.code, "message": e.message})

    def svc(request: Request) -> MemoryOpsService:
        service: MemoryOpsService = request.app.state.service
        return service

    # ---- health & state ---------------------------------------------------------------------

    @app.get("/api/health", response_model=HealthReport, tags=["system"])
    async def health() -> HealthReport:
        return await check_all(settings)

    @app.get("/api/state", tags=["plant"])
    async def state(request: Request) -> dict[str, Any]:
        return svc(request).state()

    @app.get("/api/stream", tags=["plant"])
    async def stream(
        request: Request,
        last_event_id: str | None = Header(default=None),
        since: int | None = None,
    ) -> EventSourceResponse:
        """One global Server-Sent Events stream, replaying everything after the given event ID.

        `since` (query) serves the first connection — a browser cannot set headers on it — and
        the `Last-Event-ID` header, which EventSource sends on every reconnect, wins over it.
        """
        service = svc(request)
        last = int(last_event_id) if last_event_id and last_event_id.isdigit() else since

        async def events() -> AsyncIterator[dict[str, str]]:
            async for event in service.bus.subscribe(last):
                if await request.is_disconnected():
                    break
                yield {"id": str(event.id), "data": event.model_dump_json()}

        return EventSourceResponse(events(), ping=15)

    @app.get("/api/plant/history", tags=["plant"])
    async def plant_history(
        request: Request, metric: Metric = "throughput_pct", minutes: float = 10
    ) -> dict[str, list[dict[str, float]]]:
        """Recent history per machine (sim time, 10 s grid, at most 60 points)."""
        return svc(request).plant_history(metric, minutes)

    # ---- incidents --------------------------------------------------------------------------

    @app.post("/api/incident/predefined", response_model=IncidentView, tags=["incidents"])
    async def predefined(body: PredefinedRequest, request: Request) -> IncidentView:
        if body.type not in PREDEFINED_TYPES + SITE_KNOWLEDGE_TYPES:
            raise ServiceError(422, "INVALID_TYPE", "use the custom builder for other incidents")
        return svc(request).trigger(
            source="predefined",
            memory_enabled=body.memory_enabled,
            incident_type=body.type,
            machine=body.machine,
        )

    @app.post("/api/incident/random", response_model=IncidentView, tags=["incidents"])
    async def random_incident(body: RandomRequest, request: Request) -> IncidentView:
        return svc(request).trigger(source="random", memory_enabled=body.memory_enabled)

    @app.post("/api/incident/custom", response_model=IncidentView, tags=["incidents"])
    async def custom(body: CustomRequest, request: Request) -> IncidentView:
        return svc(request).trigger(
            source="custom", memory_enabled=body.memory_enabled, custom=body.spec
        )

    @app.post(
        "/api/incident/{incident_id}/action",
        response_model=Accepted,
        status_code=202,
        tags=["incidents"],
    )
    async def act(incident_id: str, body: ActionRequest, request: Request) -> Accepted:
        """Approve (or override) the recommendation. Progress arrives on the stream."""
        await svc(request).act(incident_id, body.action)
        return Accepted(incident_id=incident_id)

    @app.post("/api/incident/{incident_id}/ignore", response_model=Accepted, tags=["incidents"])
    async def ignore(incident_id: str, request: Request) -> Accepted:
        svc(request).ignore(incident_id)
        return Accepted(incident_id=incident_id)

    @app.get("/api/incidents", tags=["incidents"])
    async def incidents(request: Request) -> list[dict[str, Any]]:
        return svc(request).incidents()

    @app.get("/api/incidents/{incident_id}", tags=["incidents"])
    async def incident(incident_id: str, request: Request) -> dict[str, Any]:
        return svc(request).incident(incident_id)

    # ---- memory, learning, spend ------------------------------------------------------------

    @app.get("/api/memory/records", tags=["memory"])
    async def memory_records(request: Request) -> list[dict[str, Any]]:
        """Every episode and lesson, with the exact text retained to Hindsight."""
        return svc(request).memory_records()

    @app.get("/api/memory/runbook", tags=["memory"])
    async def runbook(request: Request) -> dict[str, Any]:
        """The Incident Patterns mental model: the runbook the agent wrote itself."""
        return await svc(request).runbook()

    @app.get("/api/metrics", tags=["learning"])
    async def metric_series(request: Request) -> list[dict[str, Any]]:
        return svc(request).metric_series()

    @app.get("/api/eval/latest", tags=["learning"])
    async def eval_latest(request: Request) -> dict[str, Any]:
        """Summary of the latest committed evaluation report (BUILD_PLAN 11.4)."""
        return svc(request).eval_latest()

    @app.get("/api/usage", tags=["system"])
    async def usage(request: Request) -> dict[str, Any]:
        """LLM spend and tokens: all-time, this session, by model; the spend cap."""
        return svc(request).usage()

    # ---- admin ------------------------------------------------------------------------------

    @app.post("/api/admin/reset", tags=["system"])
    async def reset(body: ResetRequest, request: Request) -> dict[str, Any]:
        return await svc(request).reset(body.bank, body.wipe_memory)

    return app


app = create_app()
