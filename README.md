# MemoryOps

**A self-learning production incident commander.** A simulated 5-machine factory develops incidents; a single LangGraph agent investigates, recommends a fix, and — through [Hindsight](https://hindsight.vectorize.io) memory — resolves recurring incident classes faster and more accurately every time.

> Status: **Phase 2 done** — LangGraph agent with two Hindsight memory touchpoints, human-in-the-loop, trap-fix detection and retry, Langfuse tracing; 158 tests; live dry runs in BUILD_PLAN.md 14.1b. See [BUILD_PLAN.md](BUILD_PLAN.md) for the full specification and [TECH_STACK.md](TECH_STACK.md) for the stack and decision log.

## Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20+
- API keys: Hindsight Cloud, OpenAI (primary LLM), Groq (fallback LLM), Langfuse (see `.env.example`)

## Setup

```bash
cp .env.example .env        # fill in your keys
uv sync                     # backend deps
cd frontend && npm install  # frontend deps
```

## Run

```bash
# backend (http://localhost:8000)
uv run uvicorn backend.main:app --reload

# frontend (http://localhost:3000)
cd frontend && npm run dev
```

Check `http://localhost:8000/api/health` — every dependency should report `ok`.

## Phase 0.5 spikes

```bash
uv run python scripts/spike_hindsight.py   # recall scores, retain latency, observation lag
uv run python scripts/spike_groq.py        # models, tool calling, malformed-call errors
uv run python scripts/spike_recall_design.py sig   # record/query design vs match separation
```

## Live dry run (agent end to end on Groq + Hindsight + Langfuse)

```bash
uv run python scripts/demo_dryrun.py   # 6-incident demo storyline on a throwaway memory bank
make usage                               # LLM spend + tokens: all-time, by model, by step, per run
```

Every LLM call is recorded (tokens, cost, latency, incident) in `data/usage.db`; LLM calls stop at `LLM_SPEND_CAP_USD` (default $10) and the agent escalates instead.

## Tests

```bash
make check      # ruff + format check + eslint + mypy --strict + pytest (what CI runs)
make test       # pytest only — no network, no API keys
```

## How Hindsight memory is used

_To be written as the agent is built — see BUILD_PLAN.md Section 6._
