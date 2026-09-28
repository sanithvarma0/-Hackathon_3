"""Token and spend accounting: ledger, pricing, spend cap, per-provider request parameters."""

from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from backend.config import Settings
from backend.llm import LLMBudgetExceeded, LLMClient, ModelRoute, build_llm
from backend.usage import PRICES, CallUsage, UsageLedger, cost_usd, current_incident
from tests.fakes import completion

REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def usage_completion(prompt: int, completion_tokens: int, cached: int = 0, reasoning: int = 0):
    resp = completion("ok")
    resp.usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion_tokens,
        total_tokens=prompt + completion_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning),
    )
    return resp


async def no_sleep(_: float) -> None:
    return None


def test_cost_uses_published_prices_and_cached_discount():
    # gpt-5.4-mini: $0.75 in, $0.075 cached in, $4.50 out per 1M (OpenAI pricing, 2026-09-28)
    assert PRICES["gpt-5.4-mini"].input == 0.75
    cost = cost_usd("gpt-5.4-mini", prompt=10_000, cached=4_000, completion=1_000)
    assert cost == pytest.approx((6_000 * 0.75 + 4_000 * 0.075 + 1_000 * 4.50) / 1e6)
    assert cost_usd("openai/gpt-oss-120b", 10_000, 0, 1_000) == 0.0  # Groq free plan
    assert cost_usd("some-unpriced-model", 10, 0, 10) is None


async def test_every_call_is_recorded_with_tokens_cost_and_incident():
    ledger = UsageLedger(":memory:", run_label="test")

    async def create(**_: Any) -> Any:
        return usage_completion(prompt=12_000, completion_tokens=800, cached=2_000, reasoning=300)

    llm = LLMClient([ModelRoute("openai", "gpt-5.4-mini", create)], ledger=ledger)
    token = current_incident.set("INC-007")
    await llm.chat([{"role": "user", "content": "hi"}], name="decide")
    current_incident.reset(token)

    totals = ledger.totals(incident_id="INC-007")
    assert (totals.calls, totals.prompt_tokens, totals.cached_tokens) == (1, 12_000, 2_000)
    assert totals.completion_tokens == 800 and totals.reasoning_tokens == 300
    assert totals.cost_usd == pytest.approx(cost_usd("gpt-5.4-mini", 12_000, 2_000, 800))
    step = ledger.rows("SELECT step, provider, run_label FROM llm_usage")[0]
    assert step == ("decide", "openai", "test")
    assert llm.stats.cost_usd == pytest.approx(totals.cost_usd)


async def test_failed_attempts_are_recorded_too():
    ledger = UsageLedger(":memory:")
    outcomes = [openai.APIConnectionError(request=REQUEST), usage_completion(100, 10)]

    async def create(**_: Any) -> Any:
        out = outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    llm = LLMClient([ModelRoute("openai", "gpt-5.4-mini", create)], ledger=ledger, sleep=no_sleep)
    await llm.chat([{"role": "user", "content": "hi"}])
    ok = ledger.rows("SELECT ok, error FROM llm_usage ORDER BY ts")
    assert ok[0] == (0, "APIConnectionError") and ok[1] == (1, None)


async def test_spend_cap_stops_calls_before_they_are_made():
    ledger = UsageLedger(":memory:", spend_cap_usd=0.01)
    ledger.record_llm(
        step="x",
        provider="openai",
        model="gpt-5.4-mini",
        usage=CallUsage(prompt_tokens=20_000, completion_tokens=0),
        latency_s=0.1,
    )  # $0.015 already spent
    calls: list[Any] = []

    async def create(**kw: Any) -> Any:
        calls.append(kw)
        return completion("should not happen")

    llm = LLMClient([ModelRoute("openai", "gpt-5.4-mini", create)], ledger=ledger)
    with pytest.raises(LLMBudgetExceeded, match="spend cap"):
        await llm.chat([{"role": "user", "content": "hi"}])
    assert calls == [] and LLMBudgetExceeded.code == "LLM_BUDGET_EXCEEDED"


async def test_unsupported_parameters_are_dropped_and_remembered():
    """Measured: gpt-5.5 rejects temperature; OpenAI rejects Groq's include_reasoning."""
    seen: list[dict[str, Any]] = []

    async def create(**kw: Any) -> Any:
        seen.append(kw)
        if "temperature" in kw:
            raise openai.APIStatusError(
                "bad",
                response=httpx.Response(400, request=REQUEST),
                body={
                    "error": {
                        "code": "unsupported_value",
                        "param": "temperature",
                        "message": "Unsupported value: 'temperature'",
                    }
                },
            )
        return completion("ok")

    llm = LLMClient([ModelRoute("openai", "gpt-5.5", create)])
    assert (await llm.chat([{"role": "user", "content": "a"}])).content == "ok"
    await llm.chat([{"role": "user", "content": "b"}])
    assert "temperature" in seen[0] and "temperature" not in seen[1]
    assert "temperature" not in seen[2]  # remembered: no failed call the second time
    assert llm.stats.dropped_params == ["gpt-5.5:temperature"] and llm.stats.retries == 0


def test_each_provider_gets_its_own_request_parameters():
    s = Settings(_env_file=None, openai_api_key="sk", groq_api_key="gsk")
    llm = build_llm(s, traced=False)
    primary, fallback = llm._routes
    assert (primary.provider, primary.model) == ("openai", "gpt-5.4-mini")
    assert primary.params == {"reasoning_effort": "none"}  # tools + temperature=0 need it
    assert (fallback.provider, fallback.model) == ("groq", "openai/gpt-oss-120b")
    assert fallback.params == {"extra_body": {"include_reasoning": False}}


def test_unconfigured_providers_are_skipped():
    llm = build_llm(Settings(_env_file=None, groq_api_key="gsk"), traced=False)
    assert llm.models == ["groq:openai/gpt-oss-120b"]
