"""Token and spend accounting for every LLM and memory call (persistent ledger + hard cap).

Every LLM call is recorded with its tokens (input, cached input, output, reasoning), latency,
model, provider, the incident and agent step that made it, and its cost from `PRICES`.

Every Hindsight operation is recorded with its billable units and cost from
`HINDSIGHT_PRICES`: retains, recalls and runbook reads by tokens, runbook (mental model)
refreshes per call. Hindsight does not report billable tokens (a retain's `usage` counts its
internal LLM tokens, ~4x the billed amount; recall reports nothing), so billed tokens are
estimated from text size and labelled as estimates — the Hindsight billing page is
authoritative. `scripts/usage_report.py` summarizes the ledger.

A spend cap (`LLM_SPEND_CAP_USD`) is enforced against the ledger's all-time total, so a runaway
loop can never drain the account: once reached, LLM calls raise `BudgetExceeded` and the agent
escalates to a human like any other LLM outage.
"""

import sqlite3
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The incident the current agent run is working on (set by AgentRunner, read by the ledger).
current_incident: ContextVar[str | None] = ContextVar("current_incident", default=None)


@dataclass(frozen=True)
class Price:
    """USD per 1M tokens."""

    input: float
    cached_input: float
    output: float


PRICING_SOURCE = (
    "OpenAI: developers.openai.com/api/docs/pricing, Standard tier, fetched 2026-09-28. "
    "Groq: account is on the free plan, so calls cost $0 (tokens still counted)."
)
PRICES: dict[str, Price] = {
    "gpt-5.4-mini": Price(0.75, 0.075, 4.50),
    "gpt-5.4-nano": Price(0.20, 0.02, 1.25),
    "gpt-5.4": Price(2.50, 0.25, 15.00),
    "gpt-4.1-mini": Price(0.40, 0.10, 1.60),
    "openai/gpt-oss-120b": Price(0.0, 0.0, 0.0),
    "openai/gpt-oss-20b": Price(0.0, 0.0, 0.0),
    "qwen/qwen3.8-27b": Price(0.0, 0.0, 0.0),
}


@dataclass(frozen=True)
class HindsightPrice:
    """Hindsight Cloud operation rates (USD)."""

    retain_per_m: float
    recall_per_m: float
    mm_retrieve_per_m: float
    mm_refresh_per_call: float


HINDSIGHT_PRICING_SOURCE = "Hindsight Cloud billing page, Operation rates, read 2026-09-29."
HINDSIGHT_PRICES = HindsightPrice(
    retain_per_m=10.00, recall_per_m=0.75, mm_retrieve_per_m=0.25, mm_refresh_per_call=0.05
)
CHARS_PER_TOKEN = 4  # estimate for English + log text; Hindsight does not report billed tokens


def estimate_tokens(text: str) -> int:
    return round(len(text) / CHARS_PER_TOKEN)


def memory_cost_usd(op: str, billed_tokens: int, calls: int) -> float:
    p = HINDSIGHT_PRICES
    if op.startswith("retain"):
        return billed_tokens * p.retain_per_m / 1e6
    if op == "recall":
        return billed_tokens * p.recall_per_m / 1e6
    if op == "mm_retrieve":
        return billed_tokens * p.mm_retrieve_per_m / 1e6
    if op == "mm_refresh":
        return calls * p.mm_refresh_per_call
    return 0.0


def cost_usd(model: str, prompt: int, cached: int, completion: int) -> float | None:
    """Cost of one call, or None when the model has no known price (reported as unpriced)."""
    price = PRICES.get(model)
    if price is None:
        return None
    uncached = max(0, prompt - cached)
    return (uncached * price.input + cached * price.cached_input + completion * price.output) / 1e6


@dataclass(frozen=True)
class CallUsage:
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0  # includes reasoning tokens (billed as output)
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @classmethod
    def from_response(cls, resp: Any) -> "CallUsage":
        usage = getattr(resp, "usage", None)
        if usage is None:
            return cls()
        prompt_details = getattr(usage, "prompt_tokens_details", None)
        completion_details = getattr(usage, "completion_tokens_details", None)
        return cls(
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            cached_tokens=int(getattr(prompt_details, "cached_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            reasoning_tokens=int(getattr(completion_details, "reasoning_tokens", 0) or 0),
        )


SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_usage (
    ts REAL NOT NULL,
    run_label TEXT NOT NULL,
    incident_id TEXT,
    step TEXT NOT NULL,                 -- agent step that made the call (investigate, decide, ...)
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    ok INTEGER NOT NULL,
    error TEXT,
    prompt_tokens INTEGER NOT NULL,
    cached_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    reasoning_tokens INTEGER NOT NULL,
    cost_usd REAL,                      -- NULL = model has no known price
    latency_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_usage (
    ts REAL NOT NULL,
    run_label TEXT NOT NULL,
    incident_id TEXT,
    op TEXT NOT NULL,  -- retain_episode|retain_lesson|recall|mm_retrieve|mm_refresh
    input_tokens INTEGER NOT NULL,      -- Hindsight's internal LLM tokens (reported on retain)
    output_tokens INTEGER NOT NULL
);
"""

# Columns added after the first release; existing ledgers are migrated in place.
MEMORY_COLUMNS = {
    "billed_tokens": "INTEGER NOT NULL DEFAULT 0",  # estimated billable tokens
    "calls": "INTEGER NOT NULL DEFAULT 1",
    "cost_usd": "REAL NOT NULL DEFAULT 0",  # rows before the migration: unknown, left at 0
}


class BudgetExceeded(Exception):
    """The configured spend cap has been reached."""


@dataclass(frozen=True)
class Totals:
    calls: int
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cost_usd: float
    unpriced_calls: int
    memory_tokens: int
    memory_billed_tokens: int = 0
    memory_cost_usd: float = 0.0
    memory_refreshes: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def total_cost_usd(self) -> float:
        return self.cost_usd + self.memory_cost_usd


class UsageLedger:
    def __init__(
        self, path: Path | str, *, run_label: str = "live", spend_cap_usd: float | None = None
    ) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.executescript(SCHEMA)
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(memory_usage)")}
        for name, decl in MEMORY_COLUMNS.items():
            if name not in have:
                self._conn.execute(f"ALTER TABLE memory_usage ADD COLUMN {name} {decl}")
        self._conn.commit()
        self._lock = threading.Lock()
        self.run_label = run_label
        self.spend_cap_usd = spend_cap_usd

    def check_budget(self) -> None:
        if self.spend_cap_usd is None:
            return
        spent = self.spent_usd()
        if spent >= self.spend_cap_usd:
            raise BudgetExceeded(
                f"LLM spend cap reached: ${spent:.4f} of ${self.spend_cap_usd:.2f} "
                f"(raise LLM_SPEND_CAP_USD to continue)"
            )

    def record_llm(
        self,
        *,
        step: str,
        provider: str,
        model: str,
        usage: CallUsage,
        latency_s: float,
        error: str | None = None,
    ) -> float | None:
        cost = cost_usd(model, usage.prompt_tokens, usage.cached_tokens, usage.completion_tokens)
        with self._lock:
            self._conn.execute(
                "INSERT INTO llm_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    self.run_label,
                    current_incident.get(),
                    step,
                    provider,
                    model,
                    int(error is None),
                    error,
                    usage.prompt_tokens,
                    usage.cached_tokens,
                    usage.completion_tokens,
                    usage.reasoning_tokens,
                    cost,
                    int(latency_s * 1000),
                ),
            )
            self._conn.commit()
        return cost

    def record_memory(
        self,
        *,
        op: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        billed_tokens: int = 0,
        calls: int = 1,
    ) -> float:
        cost = memory_cost_usd(op, billed_tokens, calls)
        with self._lock:
            self._conn.execute(
                "INSERT INTO memory_usage (ts, run_label, incident_id, op, input_tokens, "
                "output_tokens, billed_tokens, calls, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    self.run_label,
                    current_incident.get(),
                    op,
                    input_tokens,
                    output_tokens,
                    billed_tokens,
                    calls,
                    cost,
                ),
            )
            self._conn.commit()
        return cost

    def spent_usd(self) -> float:
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage").fetchone()
        return float(row[0])

    def totals(self, *, run_label: str | None = None, incident_id: str | None = None) -> Totals:
        where, args = [], []
        if run_label is not None:
            where.append("run_label = ?")
            args.append(run_label)
        if incident_id is not None:
            where.append("incident_id = ?")
            args.append(incident_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._lock:
            llm = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(prompt_tokens), 0), "
                "COALESCE(SUM(cached_tokens), 0), COALESCE(SUM(completion_tokens), 0), "
                " COALESCE(SUM(reasoning_tokens), 0), "
                "COALESCE(SUM(cost_usd), 0), COALESCE(SUM(cost_usd IS NULL AND ok = 1), 0) "
                f"FROM llm_usage {clause}",  # noqa: S608 - clause built from fixed fragments
                args,
            ).fetchone()
            mem = self._conn.execute(
                "SELECT COALESCE(SUM(input_tokens + output_tokens), 0), "
                "COALESCE(SUM(billed_tokens), 0), COALESCE(SUM(cost_usd), 0), "
                "COALESCE(SUM(CASE WHEN op = 'mm_refresh' THEN calls ELSE 0 END), 0) "
                f"FROM memory_usage {clause}",  # noqa: S608
                args,
            ).fetchone()
        return Totals(
            calls=llm[0],
            prompt_tokens=llm[1],
            cached_tokens=llm[2],
            completion_tokens=llm[3],
            reasoning_tokens=llm[4],
            cost_usd=float(llm[5]),
            unpriced_calls=llm[6],
            memory_tokens=mem[0],
            memory_billed_tokens=mem[1],
            memory_cost_usd=float(mem[2]),
            memory_refreshes=mem[3],
        )

    def rows(self, sql: str, args: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        """Read-only access for reports."""
        with self._lock:
            return list(self._conn.execute(sql, args).fetchall())
