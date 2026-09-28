"""LLMClient: retry, fallback, budget, and Groq's malformed-output errors (BUILD_PLAN 7.5)."""

from typing import Any

import httpx
import openai
import pytest

from backend.llm import LLMClient, LLMUnavailable, ModelRoute, strip_think
from tests.fakes import completion

REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
TOOLS = [{"type": "function", "function": {"name": "get_error_logs", "parameters": {}}}]


def status_error(status: int, code: str, failed_generation: str | None = None) -> Exception:
    error: dict[str, Any] = {"message": code, "code": code}
    if failed_generation is not None:
        error["failed_generation"] = failed_generation
    return openai.APIStatusError(
        code, response=httpx.Response(status, request=REQUEST), body={"error": error}
    )


class Script:
    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.models: list[str] = []

    async def create(self, **kw: Any) -> Any:
        self.models.append(kw["model"])
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


async def no_sleep(_: float) -> None:
    return None


def routes(create: Any, *models: str) -> list[ModelRoute]:
    return [ModelRoute("fake", m, create) for m in models]


def client(script: Script, **kw: Any) -> LLMClient:
    return LLMClient(routes(script.create, "primary", "fallback"), sleep=no_sleep, **kw)


async def test_retries_transient_errors_then_succeeds():
    script = Script(
        status_error(503, "overloaded"), status_error(502, "bad_gateway"), completion("ok")
    )
    llm = client(script)
    resp = await llm.chat([{"role": "user", "content": "hi"}])
    assert resp.content == "ok" and resp.model == "primary"
    assert llm.stats.retries == 2


async def test_rate_limit_switches_to_the_fallback_immediately():
    """Each Groq model has its own TPM quota (measured: 8k TPM free tier), so switch, don't wait."""
    script = Script(status_error(429, "rate_limit_exceeded"), completion("fallback ok"))
    llm = client(script)
    resp = await llm.chat([{"role": "user", "content": "hi"}])
    assert resp.model == "fallback" and llm.stats.retries == 0


async def test_rate_limit_on_the_last_model_is_retried():
    script = Script(
        status_error(429, "rate_limit_exceeded"),
        status_error(429, "rate_limit_exceeded"),
        completion("ok"),
    )
    llm = LLMClient(routes(script.create, "only"), sleep=no_sleep)
    assert (await llm.chat([{"role": "user", "content": "hi"}])).content == "ok"


async def test_a_made_up_tool_carrying_the_answer_is_salvaged_as_text():
    """Seen live: the model "called" a tool named `json` to deliver its final JSON."""
    raw = '{"name": "json", "arguments": {"onset": "gradual", "key_signals": ["x"]}}'
    llm = client(Script(status_error(400, "tool_use_failed", raw)))
    resp = await llm.chat([{"role": "user", "content": "go"}], tools=TOOLS)
    assert resp.tool_calls == [] and resp.salvaged
    assert resp.content is not None and '"onset": "gradual"' in resp.content


async def test_falls_back_when_the_model_is_retired():
    script = Script(status_error(404, "model_not_found"), completion("from fallback"))
    llm = client(script)
    resp = await llm.chat([{"role": "user", "content": "hi"}])
    assert resp.model == "fallback" and script.models == ["primary", "fallback"]
    assert llm.stats.fallbacks == 1


async def test_everything_down_raises_llm_unavailable():
    script = Script(*[openai.APIConnectionError(request=REQUEST) for _ in range(6)])
    with pytest.raises(LLMUnavailable):
        await client(script).chat([{"role": "user", "content": "hi"}])
    assert script.models == ["primary"] * 3 + ["fallback"] * 3


async def test_budget_caps_total_time():
    ticks = iter([0.0, 0.0, 31.0, 31.0, 31.0])
    script = Script(status_error(503, "overloaded"), completion("too late"))
    llm = client(script, budget_s=30, monotonic=lambda: next(ticks))
    with pytest.raises(LLMUnavailable, match="budget"):
        await llm.chat([{"role": "user", "content": "hi"}])


async def test_tool_use_failed_with_a_real_tool_call_is_salvaged():
    raw = '{"name": "get_error_logs", "arguments": {"machine_id": "M3", "window_minutes": 30}}'
    llm = client(Script(status_error(400, "tool_use_failed", raw)))
    resp = await llm.chat([{"role": "user", "content": "go"}], tools=TOOLS)
    assert resp.salvaged and resp.tool_calls[0].name == "get_error_logs"
    assert '"M3"' in resp.tool_calls[0].arguments


async def test_tool_use_failed_with_prose_becomes_a_text_reply():
    """Measured on Groq: failed_generation is usually a refusal, not JSON (BUILD_PLAN 14.1)."""
    prose = "I'm sorry, but machine Z9 is not valid."
    llm = client(Script(status_error(400, "tool_use_failed", prose)))
    resp = await llm.chat([{"role": "user", "content": "go"}], tools=TOOLS)
    assert resp.content == prose and resp.tool_calls == [] and not resp.salvaged


async def test_non_retryable_client_error_moves_to_the_next_model():
    script = Script(status_error(400, "context_length_exceeded"), completion("fallback ok"))
    resp = await client(script).chat([{"role": "user", "content": "hi"}])
    assert resp.model == "fallback"


async def test_requests_suppress_reasoning_and_pin_sampling():
    captured: dict[str, Any] = {}

    async def create(**kw: Any) -> Any:
        captured.update(kw)
        return completion("ok")

    await LLMClient(
        [ModelRoute("groq", "m", create, {"extra_body": {"include_reasoning": False}})], seed=42
    ).chat([{"role": "user", "content": "x"}], json_mode=True)
    assert captured["extra_body"] == {"include_reasoning": False}
    assert captured["temperature"] == 0.0 and captured["seed"] == 42
    assert captured["response_format"] == {"type": "json_object"}


def test_think_blocks_are_stripped():
    assert strip_think('<think>let me reason</think>{"a": 1}') == '{"a": 1}'
    assert strip_think("<think>unterminated reasoning") == ""
    assert strip_think(None) is None


async def test_last_model_waits_as_long_as_the_provider_asks():
    """Groq says "Please try again in 8.265s"; retrying after 1-2 s just burns attempts."""
    waits: list[float] = []

    async def record_sleep(s: float) -> None:
        waits.append(s)

    err = openai.APIStatusError(
        "rate",
        response=httpx.Response(429, request=REQUEST),
        body={
            "error": {
                "code": "rate_limit_exceeded",
                "message": "Rate limit reached. Please try again in 8.265s.",
            }
        },
    )
    script = Script(err, completion("ok"))
    llm = LLMClient(routes(script.create, "only"), sleep=record_sleep, budget_s=45)
    assert (await llm.chat([{"role": "user", "content": "hi"}])).content == "ok"
    assert waits and 8.2 < waits[0] < 9.0
