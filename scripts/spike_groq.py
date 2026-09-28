"""Phase 0.5 spike: confirm Groq behaviour before building llm.py.

Answers (BUILD_PLAN.md Section 13, Phase 0.5):
  1. Which models are served? Is the fallback model still available?
  2. Does a tool-calling round trip work on primary and fallback?
  3. What does a malformed tool call look like (expect HTTP 400, code "tool_use_failed",
     raw output in "failed_generation")?
  4. Does include_reasoning=False keep reasoning text out of the content?
  5. Typical latency.

Run: uv run python scripts/spike_groq.py
"""

import json
import time

import openai
from openai import OpenAI

from backend.config import get_settings

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_recent_events",
            "description": "Config deploys, calibrations, restarts and OOM kills for one machine.",
            "parameters": {
                "type": "object",
                "properties": {
                    "machine_id": {"type": "string", "enum": ["M1", "M2", "M3", "M4", "M5"]},
                    "window_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
                },
                "required": ["machine_id", "window_minutes"],
            },
        },
    }
]

ALERT = (
    "ALERT: machine M3 throughput dropped from 97% to 64% over the last 6 minutes. "
    "Investigate what changed recently."
)


def list_models(client: OpenAI, wanted: list[str]) -> None:
    ids = sorted(m.id for m in client.models.list().data)
    print(f"{len(ids)} models served:")
    for i in ids:
        print(f"  {'*' if i in wanted else ' '} {i}")
    for w in wanted:
        print(f"{w}: {'AVAILABLE' if w in ids else 'MISSING - pick another fallback'}")


def tool_round_trip(client: OpenAI, model: str) -> None:
    print(f"\n=== tool round trip on {model}")
    messages = [
        {"role": "system", "content": "You are a production incident responder. Use tools."},
        {"role": "user", "content": ALERT},
    ]
    t0 = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            extra_body={"include_reasoning": False},
        )
    except openai.APIStatusError as e:
        print(f"FAILED: HTTP {e.status_code} {e.body}")
        return
    msg = resp.choices[0].message
    print(f"latency {time.perf_counter() - t0:.2f}s, finish={resp.choices[0].finish_reason}")
    print(f"content: {msg.content!r}")
    print(f"reasoning field present: {getattr(msg, 'reasoning', None) is not None}")
    has_think = bool(msg.content and "<think>" in msg.content)
    print(f"<think> in content: {has_think}")
    for tc in msg.tool_calls or []:
        print(f"tool_call: {tc.function.name}({tc.function.arguments})")
        try:
            json.loads(tc.function.arguments)
        except json.JSONDecodeError:
            print("  -> arguments are NOT valid JSON")
        # Second turn: feed a result back and let the model conclude.
        messages += [
            msg.model_dump(exclude_none=True),
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(
                    [
                        {
                            "ts": "09:01",
                            "event_type": "config_deployed",
                            "detail": "v2.14.3 -> v2.15.0",
                        }
                    ]
                ),
            },
        ]
        t0 = time.perf_counter()
        follow = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            extra_body={"include_reasoning": False},
        )
        print(
            f"follow-up latency {time.perf_counter() - t0:.2f}s: "
            f"{(follow.choices[0].message.content or '')[:200]!r}"
        )
        break


def provoke_malformed(client: OpenAI, model: str) -> None:
    """Force a tool call while asking for arguments that violate the schema."""
    print(f"\n=== malformed tool call probe on {model}")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Call get_recent_events for machine 'Z9' with window_minutes 'lots', "
                        "and add an extra argument 'color'='blue'."
                    ),
                }
            ],
            tools=TOOLS,
            tool_choice={"type": "function", "function": {"name": "get_recent_events"}},
            extra_body={"include_reasoning": False},
        )
        tcs = resp.choices[0].message.tool_calls or []
        print(
            "no error raised; tool calls returned:",
            [(t.function.name, t.function.arguments) for t in tcs],
        )
    except openai.APIStatusError as e:
        err = (e.body or {}).get("error", e.body) if isinstance(e.body, dict) else e.body
        print(f"HTTP {e.status_code}")
        print(f"error body: {json.dumps(err, indent=2)[:800]}")


def main() -> None:
    s = get_settings()
    if not s.groq_api_key:
        raise SystemExit("GROQ_API_KEY is not set (see .env.example)")
    client = OpenAI(api_key=s.groq_api_key, base_url=s.groq_base_url, timeout=60)
    models = [s.llm_primary_model, s.llm_fallback_model]
    list_models(client, models)
    for m in models:
        tool_round_trip(client, m)
        provoke_malformed(client, m)


if __name__ == "__main__":
    main()
