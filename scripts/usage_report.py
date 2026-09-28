"""Spend and token report from the usage ledger (`make usage`).

Run: uv run python scripts/usage_report.py [--runs N]
"""

import argparse

from backend.config import get_settings
from backend.usage import PRICING_SOURCE, UsageLedger


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=10, help="how many recent runs to list")
    args = parser.parse_args()
    s = get_settings()
    ledger = UsageLedger(s.usage_db_path, spend_cap_usd=s.llm_spend_cap_usd)
    total = ledger.totals()
    spent = ledger.spent_usd()

    print(f"ledger: {s.usage_db_path}")
    print(f"pricing: {PRICING_SOURCE}\n")
    print(
        f"ALL TIME  ${spent:.4f} spent of ${s.llm_spend_cap_usd:.2f} cap "
        f"({100 * spent / s.llm_spend_cap_usd:.1f}%)"
    )
    print(
        f"          {total.calls} LLM calls, {total.total_tokens:,} tokens "
        f"({total.prompt_tokens:,} in / {total.cached_tokens:,} cached / "
        f"{total.completion_tokens:,} out / {total.reasoning_tokens:,} reasoning)"
    )
    print(f"          Hindsight retain tokens: {total.memory_tokens:,}")
    if total.unpriced_calls:
        print(f"          WARNING: {total.unpriced_calls} calls used a model with no known price")

    print("\nBY MODEL")
    for provider, model, calls, tokens, cost, failed, ms in ledger.rows(
        "SELECT provider, model, COUNT(*), SUM(prompt_tokens + completion_tokens), "
        "COALESCE(SUM(cost_usd), 0), SUM(ok = 0), AVG(latency_ms) FROM llm_usage "
        "GROUP BY provider, model ORDER BY 5 DESC"
    ):
        print(
            f"  {provider}:{model:<24} {calls:>5} calls {tokens:>10,} tokens ${cost:>8.4f}  "
            f"{failed} failed  avg {ms:.0f} ms"
        )

    print("\nBY AGENT STEP")
    for step, calls, tokens, cost in ledger.rows(
        "SELECT step, COUNT(*), SUM(prompt_tokens + completion_tokens), "
        "COALESCE(SUM(cost_usd), 0) FROM llm_usage GROUP BY step ORDER BY 4 DESC"
    ):
        print(f"  {step:<24} {calls:>5} calls {tokens:>10,} tokens ${cost:>8.4f}")

    print(f"\nRECENT RUNS (last {args.runs})")
    for label, calls, tokens, cost, incidents in ledger.rows(
        "SELECT run_label, COUNT(*), SUM(prompt_tokens + completion_tokens), "
        "COALESCE(SUM(cost_usd), 0), COUNT(DISTINCT incident_id) FROM llm_usage "
        "GROUP BY run_label ORDER BY MIN(ts) DESC LIMIT ?",
        (args.runs,),
    ):
        per = cost / incidents if incidents else 0.0
        print(
            f"  {label:<28} {incidents:>3} incidents {calls:>5} calls {tokens:>10,} tokens "
            f"${cost:>8.4f}  (${per:.4f}/incident)"
        )


if __name__ == "__main__":
    main()
