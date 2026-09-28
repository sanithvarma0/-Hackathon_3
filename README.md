# MemoryOps

**A self-learning production incident commander.** A simulated 5-machine factory develops incidents; a single LangGraph agent investigates, recommends a fix, and — through [Hindsight](https://hindsight.vectorize.io) memory — resolves recurring incident classes faster and more accurately every time.

> Status: **Phase 0 + 0.5 done** (scaffold, live dependency checks, spikes — results in BUILD_PLAN.md Section 14). See [BUILD_PLAN.md](BUILD_PLAN.md) for the full specification and [TECH_STACK.md](TECH_STACK.md) for the stack and decision log.

## Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20+
- API keys: Hindsight Cloud, Groq, Langfuse (see `.env.example`)

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

## Tests

```bash
uv run pytest
uv run ruff check .
```

## How Hindsight memory is used

_To be written as the agent is built — see BUILD_PLAN.md Section 6._
