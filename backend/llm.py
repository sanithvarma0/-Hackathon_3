"""Groq chat client with retry, model fallback and malformed-output handling (BUILD_PLAN.md 7.5).

Behaviour, each point measured in the Phase 0.5 spike (BUILD_PLAN.md 14.1):
- Primary model, then fallback model; each gets 1 + `retries` attempts with backoff.
- A per-call time budget caps all attempts; exhausting it raises `LLMUnavailable`, which the
  agent turns into an ESCALATE_HUMAN recommendation (never a crash).
- HTTP 400 `tool_use_failed` / `json_validate_failed`: Groq returns the model's raw output in
  `failed_generation`. If it parses as a tool call it is salvaged; otherwise it is returned as
  the model's text reply so the caller can correct it.
- 404 `model_not_found` skips straight to the next model (Groq retired qwen3-32b this way).
- 429 skips to the next model too: each Groq model has its own tokens-per-minute quota
  (measured: 8,000 TPM for gpt-oss-120b on the free tier), so waiting is slower than switching.
- Reasoning is suppressed (`include_reasoning: false`) and any `<think>` block is stripped.
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

THINK_RE = re.compile(r"<think>.*?(</think>|$)", re.DOTALL | re.IGNORECASE)
RETRY_IN_RE = re.compile(r"try again in ([\d.]+)\s*(ms|s)\b", re.IGNORECASE)
MAX_RATE_LIMIT_WAIT_S = 15.0
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

CreateFn = Callable[..., Awaitable[Any]]


class LLMUnavailable(Exception):
    """Every model and attempt failed within the budget."""


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
    retries: int = 0
    fallbacks: int = 0
    salvaged: int = 0
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
        create: CreateFn,
        models: list[str],
        *,
        retries: int = 2,
        backoff_s: tuple[float, ...] = (1.0, 2.0),
        budget_s: float = 30.0,
        temperature: float = 0.0,
        seed: int | None = None,
        pass_trace_name: bool = False,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._create = create
        self._models = models
        self._retries = retries
        self._backoff = backoff_s
        self._budget = budget_s
        self._temperature = temperature
        self._seed = seed
        self._pass_trace_name = pass_trace_name
        self._sleep = sleep
        self._monotonic = monotonic
        self.stats = LLMStats()

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
        for index, model in enumerate(self._models):
            if index > 0:
                self.stats.fallbacks += 1
            for attempt in range(self._retries + 1):
                wait_s: float | None = None
                if self._monotonic() >= deadline:
                    raise LLMUnavailable(f"LLM budget of {self._budget:.0f}s exhausted")
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": self._temperature,
                    "extra_body": {"include_reasoning": False},
                }
                if self._seed is not None:
                    kwargs["seed"] = self._seed
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = tool_choice or "auto"
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                if self._pass_trace_name:
                    kwargs["name"] = name
                try:
                    resp = await asyncio.wait_for(
                        self._create(**kwargs), timeout=max(1.0, deadline - self._monotonic())
                    )
                except openai.APIStatusError as e:
                    err = _error_body(e)
                    code = err.get("code")
                    self.stats.errors.append(f"{model}: HTTP {e.status_code} {code}")
                    if e.status_code == 400 and code in ("tool_use_failed", "json_validate_failed"):
                        return self._from_failed_generation(
                            str(err.get("failed_generation", "")), model, known_tools
                        )
                    if e.status_code == 429 and index < len(self._models) - 1:
                        break  # rate-limited: the next model has its own quota, use it now
                    if e.status_code == 429:
                        wait_s = _retry_after_s(e)  # last model: wait as long as asked
                    if e.status_code == 404 or e.status_code not in RETRYABLE_STATUS:
                        break  # this model can't serve the request; try the next one
                except (openai.APIConnectionError, openai.APITimeoutError, TimeoutError) as e:
                    self.stats.errors.append(f"{model}: {type(e).__name__}")
                else:
                    return self._parse(resp, model)
                if attempt < self._retries:
                    self.stats.retries += 1
                    backoff = self._backoff[min(attempt, len(self._backoff) - 1)]
                    if wait_s is not None:
                        backoff = max(backoff, min(wait_s + 0.25, MAX_RATE_LIMIT_WAIT_S))
                    if self._monotonic() + backoff >= deadline:
                        break
                    await self._sleep(backoff)
        raise LLMUnavailable("all models failed: " + "; ".join(self.stats.errors[-4:]))

    def _parse(self, resp: Any, model: str) -> LLMResponse:
        message = resp.choices[0].message
        tokens = int(getattr(getattr(resp, "usage", None), "total_tokens", 0) or 0)
        self.stats.calls += 1
        self.stats.total_tokens += tokens
        calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
            for tc in message.tool_calls or []
        ]
        return LLMResponse(
            content=strip_think(message.content),
            tool_calls=calls,
            model=model,
            total_tokens=tokens,
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


def build_llm(settings: Settings, *, traced: bool) -> LLMClient:
    """Groq through its OpenAI-compatible endpoint; `langfuse.openai` records every call."""
    if traced:
        from langfuse.openai import AsyncOpenAI as TracedAsyncOpenAI  # type: ignore[attr-defined]

        client: Any = TracedAsyncOpenAI(
            api_key=settings.groq_api_key, base_url=settings.groq_base_url, max_retries=0
        )
    else:
        client = openai.AsyncOpenAI(
            api_key=settings.groq_api_key, base_url=settings.groq_base_url, max_retries=0
        )
    # max_retries=0: LLMClient owns retry and fallback; the SDK's hidden retries would double it.
    return LLMClient(
        client.chat.completions.create,
        [settings.llm_primary_model, settings.llm_fallback_model],
        budget_s=settings.llm_call_budget_s,
        seed=settings.llm_seed,
        pass_trace_name=traced,
    )
