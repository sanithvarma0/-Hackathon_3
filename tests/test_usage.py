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


# ---- Hindsight spend ----------------------------------------------------------------------


def test_hindsight_operations_are_priced_from_the_billing_page():
    from backend.usage import HINDSIGHT_PRICES, memory_cost_usd

    assert HINDSIGHT_PRICES.retain_per_m == 10.00 and HINDSIGHT_PRICES.mm_refresh_per_call == 0.05
    assert memory_cost_usd("retain_episode", 1_000, 1) == pytest.approx(0.01)
    assert memory_cost_usd("recall", 1_000, 1) == pytest.approx(0.00075)
    assert memory_cost_usd("mm_retrieve", 1_000, 1) == pytest.approx(0.00025)
    assert memory_cost_usd("mm_refresh", 0, 2) == pytest.approx(0.10)


def test_memory_spend_is_in_the_totals_next_to_llm_spend():
    ledger = UsageLedger(":memory:", run_label="r")
    token = current_incident.set("INC-001")
    ledger.record_memory(
        op="retain_episode", input_tokens=2_000, output_tokens=400, billed_tokens=500
    )
    ledger.record_memory(op="mm_refresh", calls=1)
    current_incident.reset(token)
    t = ledger.totals(incident_id="INC-001")
    assert t.memory_tokens == 2_400  # Hindsight's internal tokens, informational
    assert t.memory_billed_tokens == 500
    assert t.memory_refreshes == 1
    assert t.memory_cost_usd == pytest.approx(0.005 + 0.05)
    assert t.total_cost_usd == pytest.approx(t.cost_usd + t.memory_cost_usd)


def test_an_existing_ledger_is_migrated_in_place(tmp_path):
    import sqlite3

    path = tmp_path / "usage.db"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE memory_usage (ts REAL NOT NULL, run_label TEXT NOT NULL, incident_id TEXT,"
        " op TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL);"
        "INSERT INTO memory_usage VALUES (1, 'old', 'INC-001', 'retain_episode', 900, 100);"
    )
    old.commit()
    old.close()
    ledger = UsageLedger(path)
    ledger.record_memory(op="recall", billed_tokens=1_000)
    t = ledger.totals()
    assert t.memory_tokens == 1_000  # the old row's tokens survive
    assert t.memory_cost_usd == pytest.approx(0.00075)  # old row: cost unknown, counted as 0


class FakeHindsightClient:
    """Just enough of hindsight_client.Hindsight for HindsightMemory's accounting paths."""

    def __init__(self) -> None:
        self.refreshed_at = "2026-09-29T10:00:00+00:00"
        self.content = "## Configuration regressions\n- Effective fix: roll back."

    async def acreate_bank(self, **_: Any) -> None:
        return None

    async def alist_mental_models(self, **_: Any) -> Any:
        return SimpleNamespace(items=[SimpleNamespace(id="incident-patterns")])

    async def aget_mental_model(self, *, detail: str, **_: Any) -> Any:
        return SimpleNamespace(
            last_refreshed_at=self.refreshed_at,
            content=self.content if detail != "metadata" else None,
        )

    async def aretain(self, **_: Any) -> Any:
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=2_000, output_tokens=400))

    async def arecall(self, **_: Any) -> Any:
        return SimpleNamespace(results=[SimpleNamespace(type="observation", text="x" * 400)])


async def test_runbook_refreshes_are_counted_once_each_and_not_at_creation():
    from backend.memory.hindsight import HindsightMemory

    client = FakeHindsightClient()
    ledger = UsageLedger(":memory:")
    memory = HindsightMemory(client, "bank", rel_rerank=0.15, min_rerank=0.05, ledger=ledger)  # type: ignore[arg-type]
    await memory.ensure_bank()
    assert not await memory.check_refresh()  # nothing rebuilt since creation
    client.refreshed_at = "2026-09-29T10:05:00+00:00"
    assert await memory.check_refresh()
    assert not await memory.check_refresh()  # same rebuild, seen twice: counted once
    assert ledger.totals().memory_refreshes == 1


async def test_retain_recall_and_runbook_reads_record_billed_estimates():
    from datetime import UTC, datetime

    from backend.memory.hindsight import HindsightMemory
    from backend.memory.store import MemoryRecord

    ledger = UsageLedger(":memory:")
    memory = HindsightMemory(
        FakeHindsightClient(), "bank", rel_rerank=0.15, min_rerank=0.05, ledger=ledger
    )  # type: ignore[arg-type]
    record = MemoryRecord(
        document_id="INC-001",
        incident_id="INC-001",
        kind="episode",
        text="e" * 2_000,
        metadata={"incident_id": "INC-001"},
        timestamp=datetime.now(UTC),
    )
    await memory.retain(record)
    await memory.recall("servo timeouts")
    await memory.runbook()
    ops = dict(ledger.rows("SELECT op, billed_tokens FROM memory_usage"))
    assert ops == {"retain_episode": 500, "recall": 100, "mm_retrieve": 14}  # ~4 chars/token


async def test_placeholder_runbook_is_not_content_and_observations_stand_in():
    from backend.memory.hindsight import HindsightMemory

    client = FakeHindsightClient()
    client.content = "Generating content...\n"  # Hindsight Cloud, 2026-09-29
    memory = HindsightMemory(client, "bank", rel_rerank=0.15, min_rerank=0.05)  # type: ignore[arg-type]
    assert await memory.runbook() is None  # never injected into the agent's prompt
    assert await memory.observations() == ["x" * 400]
