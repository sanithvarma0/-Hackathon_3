# MemoryOps

**A self-learning production incident commander.** A simulated 5-machine factory develops incidents; a single LangGraph agent investigates, recommends a fix, and — through [Hindsight](https://hindsight.vectorize.io) memory — resolves recurring incident classes faster and more accurately every time.

> Status: **Phase 5 done** — a paired memory ON/OFF evaluation on the live stack (`make eval`, reports in [`docs/eval/`](docs/eval/)). On incidents whose fix is known only from a past resolution, memory ON is right **+46 points** more often (95% CI +25 to +67), with 8.3 fewer tool calls and 15 fewer sim-minutes to recover; on textbook incidents it does no harm. 3 of 6 acceptance targets pass; the misses are reported with their causes (BUILD_PLAN.md 14.1f). 277 backend + 17 frontend tests. See [BUILD_PLAN.md](BUILD_PLAN.md) for the full specification and [TECH_STACK.md](TECH_STACK.md) for the stack and decision log.

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

## The control room

![Plant floor with a live recommendation: memory hints and matched incidents in purple](docs/screenshots/floor-recommendation.png)

| Network failure (1440×900) | Memory Browser + self-written runbook | Learning |
|---|---|---|
| ![](docs/screenshots/network-failure-1440.png) | ![](docs/screenshots/memory-browser.png) | ![](docs/screenshots/learning.png) |

Screenshots from a real session against live services (BUILD_PLAN.md 14.1e) — including the incident where memory misled the agent.

## Evaluation

```bash
make eval-quick   # 1 seed x 18 incidents x memory ON/OFF, ~15 min, ~$1.5
make eval         # 3 seeds x 24 incidents x memory ON/OFF, ~45 min, ~$5 (LLM + Hindsight)
```

Each run writes `docs/eval/<date>-<sha>/REPORT.md` (targets PASS/FAIL, learning by exposure with 95% CIs, per family and class, transfer, discrimination, retrieval, calibration, cost, every failure with a Langfuse trace link), `results.json` and charts. The Learning tab shows the latest. CI runs the same pipeline with a fake LLM and fake memory.

## Run

```bash
# backend (http://localhost:8000)
uv run uvicorn backend.main:app --reload

# frontend — the control room (http://localhost:3000)
cd frontend && npm run dev

# or both at once
make dev
```

Check `http://localhost:8000/api/health` — every dependency should report `ok`.

Drive an incident from the terminal (the UI does the same over the same API):

```bash
curl -N localhost:8000/api/stream &                                   # live events
curl -XPOST localhost:8000/api/incident/predefined \
     -H 'content-type: application/json' -d '{"type":"config_regression","machine":"M3"}'
curl localhost:8000/api/incidents/INC-001                             # pending recommendation
curl -XPOST localhost:8000/api/incident/INC-001/action \
     -H 'content-type: application/json' -d '{"action":"ROLLBACK_CONFIG"}'
curl localhost:8000/api/usage                                         # tokens + spend
```

API reference: `http://localhost:8000/docs`.

## Deploy

Backend on Render (Docker, persistent disk, `render.yaml`) and UI on Vercel (root `frontend`), with a demo passcode and a trigger rate limit protecting spend. Local fallback: `docker compose up --build`. Full steps in [docs/DEPLOY.md](docs/DEPLOY.md).

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
