"""LLM client: provider routes, retry, fallback, spend cap, malformed-output handling.

Routes (BUILD_PLAN.md 7.5, TECH_STACK.md v1.8): OpenAI `gpt-5.4-mini` primary, Groq
`gpt-oss-120b` fallback — two providers, so one provider's outage or rate limit never stops the
agent. Each route carries its own request parameters (OpenAI rejects Groq's `include_reasoning`;
measured).

Behaviour, each point measured live (BUILD_PLAN.md 14.1, 14.1b):
- Each route gets 1 + `retries` attempts with backoff, under a per-call time budget; exhausting
  everything raises `LLMUnavailable`, which the agent turns into an escalation (never a crash).
- 429 on a route with a successor: switch now (separate quota). On the last route: wait as long
  as the provider asks ("try again in 8.265s"), capped.
- 404 / other non-retryable 4xx: next route.
- 400 naming an unsupported parameter: drop that parameter for this route and retry at once
  (e.g. some models reject `temperature`); remembered for later calls.
- 400 `tool_use_failed` / `json_validate_failed`: salvage the model's raw output — a real tool
  call is executed, a made-up "json" tool carrying the answer becomes the text reply.
- Every attempt is written to the usage ledger (tokens, cost, latency, errors), and the ledger's
  spend cap is checked before every attempt.
"""

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import openai

from backend.config import Settings
from backend.usage import BudgetExceeded, CallUsage, UsageLedger

THINK_RE = re.compile(r"<think>.*?(</think>|$)", re.DOTALL | re.IGNORECASE)
RETRY_IN_RE = re.compile(r"try again in ([\d.]+)\s*(ms|s)\b", re.IGNORECASE)
MAX_RATE_LIMIT_WAIT_S = 15.0
MAX_PARAM_DROPS = 3
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
UNSUPPORTED_CODES = {"unknown_parameter", "unsupported_parameter", "unsupported_value"}

CreateFn = Callable[..., Awaitable[Any]]


class LLMUnavailable(Exception):
    """Every route and attempt failed within the budget (or the spend cap was reached)."""

    code = "LLM_UNAVAILABLE"


class LLMBudgetExceeded(LLMUnavailable):
    code = "LLM_BUDGET_EXCEEDED"


@dataclass(frozen=True)
class ModelRoute:
    provider: str  # "openai" | "groq"
    model: str
    create: CreateFn
    params: dict[str, Any] = field(default_factory=dict)  # provider-specific request params


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string, validated by the caller


@dataclass(frozen=True)
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall]
    model: str
    total_tokens: int = 0
    salvaged: bool = False  # recovered from a tool_use_failed error


@dataclass
class LLMStats:
    calls: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    retries: int = 0
    fallbacks: int = 0
    salvaged: int = 0
    dropped_params: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def strip_think(text: str | None) -> str | None:
    if text is None:
        return None
    return THINK_RE.sub("", text).strip()


def _error_body(e: openai.APIStatusError) -> dict[str, Any]:
    body = e.body if isinstance(e.body, dict) else {}
    inner = body.get("error", body)
    return inner if isinstance(inner, dict) else {}


def _retry_after_s(e: openai.APIStatusError) -> float | None:
    """How long the provider asked us to wait (Retry-After header, or Groq's message text)."""
    header = e.response.headers.get("retry-after") if e.response is not None else None
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    match = RETRY_IN_RE.search(str(_error_body(e).get("message", "")))
    if match:
        value = float(match.group(1))
        return value / 1000 if match.group(2).lower() == "ms" else value
    return None


def _unsupported_param(e: openai.APIStatusError, kwargs: dict[str, Any]) -> str | None:
    """The request parameter a 400 says the model doesn't accept, if it's one we sent."""
    if e.status_code != 400:
        return None
    err = _error_body(e)
    param = err.get("param")
    message = str(err.get("message", ""))
    candidates = [param] if isinstance(param, str) else []
    candidates += re.findall(r"'([a-z_]+)'", message)
    if err.get("code") not in UNSUPPORTED_CODES and "not supported" not in message.lower():
        return None
    extra = kwargs.get("extra_body") or {}
    for name in candidates:
        if name in kwargs or name in extra:
            return name
    return None


def _salvage(failed_generation: str, known_tools: set[str]) -> ToolCall | str | None:
    """Recover what the model meant when the provider rejected its output on format grounds.

    - a call to a real tool                     -> that ToolCall
    - a call to a made-up tool (seen live: the model "called" a tool named `json` to deliver
      its final answer)                          -> the arguments, as the text reply
    - anything else                              -> None (caller keeps the raw text)
    """
    try:
        obj = json.loads(failed_generation)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict) or "name" not in obj:
        return None
    args = obj.get("arguments", obj.get("parameters", {}))
    if obj["name"] in known_tools:
        return ToolCall(
            id="salvaged",
            name=obj["name"],
            arguments=args if isinstance(args, str) else json.dumps(args),
        )
    return args if isinstance(args, str) else json.dumps(args)


class LLMClient:
    def __init__(
        self,
        routes: list[ModelRoute],
        *,
        retries: int = 2,
        backoff_s: tuple[float, ...] = (1.0, 2.0),
        budget_s: float = 45.0,
        temperature: float = 0.0,
        seed: int | None = None,
        pass_trace_name: bool = False,
        ledger: UsageLedger | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._routes = routes
        self._retries = retries
        self._backoff = backoff_s
        self._budget = budget_s
        self._temperature = temperature
        self._seed = seed
        self._pass_trace_name = pass_trace_name
        self._ledger = ledger
        self._sleep = sleep
        self._monotonic = monotonic
        self._dropped: dict[tuple[str, str], set[str]] = {}
        self.stats = LLMStats()

    @property
    def models(self) -> list[str]:
        return [f"{r.provider}:{r.model}" for r in self._routes]

    def _kwargs(
        self,
        route: ModelRoute,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str | None,
        json_mode: bool,
        name: str,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": route.model,
            "messages": messages,
            "temperature": self._temperature,
        }
        if self._seed is not None:
            kwargs["seed"] = self._seed
        for key, value in route.params.items():
            kwargs[key] = dict(value) if isinstance(value, dict) else value
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if self._pass_trace_name:
            kwargs["name"] = name
        for param in self._dropped.get((route.provider, route.model), set()):
            kwargs.pop(param, None)
            if isinstance(kwargs.get("extra_body"), dict):
                kwargs["extra_body"].pop(param, None)
        return kwargs

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        json_mode: bool = False,
        name: str = "llm",
    ) -> LLMResponse:
        known_tools = {t["function"]["name"] for t in tools or []}
        deadline = self._monotonic() + self._budget
        for index, route in enumerate(self._routes):
            if index > 0:
                self.stats.fallbacks += 1
            drops = 0
            attempt = 0
            while attempt <= self._retries:
                if self._ledger is not None:
                    try:
                        self._ledger.check_budget()
                    except BudgetExceeded as e:
                        raise LLMBudgetExceeded(str(e)) from e
                if self._monotonic() >= deadline:
                    raise LLMUnavailable(f"LLM budget of {self._budget:.0f}s exhausted")
                kwargs = self._kwargs(route, messages, tools, tool_choice, json_mode, name)
                wait_s: float | None = None
                started = time.perf_counter()
                try:
                    resp = await asyncio.wait_for(
                        route.create(**kwargs), timeout=max(1.0, deadline - self._monotonic())
                    )
                except openai.APIStatusError as e:
                    err = _error_body(e)
                    code = err.get("code")
                    self._record(route, name, CallUsage(), started, f"HTTP {e.status_code} {code}")
                    param = _unsupported_param(e, kwargs)
                    if param is not None and drops < MAX_PARAM_DROPS:
                        drops += 1
                        self._dropped.setdefault((route.provider, route.model), set()).add(param)
                        self.stats.dropped_params.append(f"{route.model}:{param}")
                        continue  # same attempt, without the parameter
                    if e.status_code == 400 and code in ("tool_use_failed", "json_validate_failed"):
                        return self._from_failed_generation(
                            str(err.get("failed_generation", "")), route.model, known_tools
                        )
                    if e.status_code == 429 and index < len(self._routes) - 1:
                        break  # rate-limited: the next route has its own quota, use it now
                    if e.status_code == 429:
                        wait_s = _retry_after_s(e)  # last route: wait as long as asked
                    elif e.status_code == 404 or e.status_code not in RETRYABLE_STATUS:
                        break  # this route can't serve the request; try the next one
                except (openai.APIConnectionError, openai.APITimeoutError, TimeoutError) as e:
                    self._record(route, name, CallUsage(), started, type(e).__name__)
                else:
                    usage = CallUsage.from_response(resp)
                    self._record(route, name, usage, started, None)
                    return self._parse(resp, route.model, usage)
                if attempt < self._retries:
                    self.stats.retries += 1
                    backoff = self._backoff[min(attempt, len(self._backoff) - 1)]
                    if wait_s is not None:
                        backoff = max(backoff, min(wait_s + 0.25, MAX_RATE_LIMIT_WAIT_S))
                    if self._monotonic() + backoff >= deadline:
                        break
                    await self._sleep(backoff)
                attempt += 1
        raise LLMUnavailable("all models failed: " + "; ".join(self.stats.errors[-4:]))

    def _record(
        self, route: ModelRoute, step: str, usage: CallUsage, started: float, error: str | None
    ) -> None:
        if error is not None:
            self.stats.errors.append(f"{route.model}: {error}")
        if self._ledger is None:
            return
        cost = self._ledger.record_llm(
            step=step,
            provider=route.provider,
            model=route.model,
            usage=usage,
            latency_s=time.perf_counter() - started,
            error=error,
        )
        if cost:
            self.stats.cost_usd += cost

    def _parse(self, resp: Any, model: str, usage: CallUsage) -> LLMResponse:
        message = resp.choices[0].message
        self.stats.calls += 1
        self.stats.total_tokens += usage.total_tokens
        calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
            for tc in message.tool_calls or []
        ]
        return LLMResponse(
            content=strip_think(message.content),
            tool_calls=calls,
            model=model,
            total_tokens=usage.total_tokens,
        )

    def _from_failed_generation(
        self, failed_generation: str, model: str, known_tools: set[str]
    ) -> LLMResponse:
        self.stats.calls += 1
        salvaged = _salvage(failed_generation, known_tools)
        if isinstance(salvaged, ToolCall):
            self.stats.salvaged += 1
            return LLMResponse(content=None, tool_calls=[salvaged], model=model, salvaged=True)
        if isinstance(salvaged, str):
            self.stats.salvaged += 1
            return LLMResponse(content=salvaged, tool_calls=[], model=model, salvaged=True)
        return LLMResponse(content=strip_think(failed_generation), tool_calls=[], model=model)


def _route_params(provider: str, model: str, settings: Settings) -> dict[str, Any]:
    if provider == "groq":
        return {"extra_body": {"include_reasoning": False}}
    if model.startswith("gpt-5") or re.match(r"o\d", model):
        return {"reasoning_effort": settings.openai_reasoning_effort}
    return {}


def build_llm(settings: Settings, *, traced: bool, ledger: UsageLedger | None = None) -> LLMClient:
    """OpenAI primary + Groq fallback, both through the OpenAI SDK (`langfuse.openai` traces)."""
    client_class: Any = openai.AsyncOpenAI
    if traced:
        from langfuse.openai import AsyncOpenAI as TracedAsyncOpenAI  # type: ignore[attr-defined]

        client_class = TracedAsyncOpenAI
    providers = {
        "openai": (settings.openai_api_key, settings.openai_base_url),
        "groq": (settings.groq_api_key, settings.groq_base_url),
    }
    clients: dict[str, Any] = {}
    routes = []
    for provider, model in (
        (settings.llm_primary_provider, settings.llm_primary_model),
        (settings.llm_fallback_provider, settings.llm_fallback_model),
    ):
        key, base_url = providers[provider]
        if not key:
            continue  # provider not configured (e.g. CI): skip its route
        if provider not in clients:
            # max_retries=0: LLMClient owns retry and fallback; SDK retries would double it.
            clients[provider] = client_class(api_key=key, base_url=base_url, max_retries=0)
        routes.append(
            ModelRoute(
                provider,
                model,
                clients[provider].chat.completions.create,
                _route_params(provider, model, settings),
            )
        )
    return LLMClient(
        routes,
        budget_s=settings.llm_call_budget_s,
        seed=settings.llm_seed,
        pass_trace_name=traced,
        ledger=ledger,
    )
