# 🔒 TECH STACK — FROZEN (v1.2)

All decisions are locked. This is the definitive reference for the rest of the hackathon. BUILD_PLAN.md is the implementation spec; this file is the "what and why".

> **v1.2 changes:** reviewed the suggested reference repositories (new section below); "Incident Patterns" mental model is now core; Groq error/reasoning handling pinned to what Hindsight's own Groq provider does; no stack additions — every reviewed framework/template was either a pattern source or rejected.
>
> **v1.1 changes:** named the actual Hindsight SDK (`hindsight-client`) and dropped the `hindsight-langgraph` prebuilt nodes; Groq accessed through the OpenAI SDK so Langfuse tracing is a drop-in; human-in-the-loop via LangGraph `interrupt()`; `reflect()` removed from the core path (mental model is a stretch); LLM memory tool removed; node list and repo structure synced with BUILD_PLAN.md.

---

## The Stack

| # | Layer | Choice | Version / Detail |
|---|---|---|---|
| 1 | **Agent Runtime** | LangGraph (raw `StateGraph`, no prebuilt agents) | Single agent, custom graph: `recall_hints → investigate → search_memory → decide → act → verify → learn` (+ `record_lesson` retry loop) |
| 2 | **Memory** | Hindsight Cloud | `hindsight-client` (Python) v0.10.x · API `https://api.hindsight.vectorize.io` · promo `MEMHACK99` ($50 credits) |
| 3 | **Memory operations used** | `create_bank`, `retain`, `recall`, `create_mental_model` + read (all core) · `delete_bank` (reset) | Observations (auto-consolidation) consumed via `recall(types=[..., "observation"])`; "Incident Patterns" mental model auto-refreshes after consolidation |
| 4 | **Decision Logic** | LangGraph conditional edges | No external routing framework |
| 5 | **Human-in-the-loop** | LangGraph `interrupt()` + `InMemorySaver` checkpointer | `thread_id = incident_id`, resume with `Command(resume=...)` |
| 6 | **Harness** | Custom-built (~150 lines) | Reference: `agent-service-toolkit` (JoshuaC215) |
| 7 | **Protocol** | None — FastAPI REST + one global SSE stream | `sse-starlette`, Last-Event-ID replay |
| 8 | **LLM** | Groq via OpenAI-compatible API | `openai` SDK, `base_url=https://api.groq.com/openai/v1` · Primary `openai/gpt-oss-120b` · Fallback `qwen/qwen3-32b` (env-configurable; availability confirmed in Phase 0.5) · handles `tool_use_failed` (HTTP 400 + `failed_generation`) · `include_reasoning: false` |
| 9 | **Observability** | Langfuse Cloud (free tier) | `from langfuse.openai import OpenAI` (auto-traces LLM calls) + `@observe` on graph nodes/tools |
| 10 | **Database** | SQLite | Simulator state, metrics, episode outbox |
| 11 | **Vector Store** | None | Hindsight is the vector store |
| 12 | **Semantic Layer** | None | Hindsight's 4-way retrieval (semantic + BM25 + graph + temporal, reranked) |
| 13 | **Backend** | FastAPI | Python 3.11+, Pydantic v2, `pydantic-settings` |
| 14 | **Frontend** | Next.js (App Router) + Tailwind + shadcn/ui + Recharts | Ops dashboard, not a chat |
| 15 | **Guardrails** | 6 custom rules | Pydantic validation, tool-call cap, action whitelist, dependency retry/fallback, simulator state validation, memory match threshold |
| 16 | **Evaluation** | Custom tracker + headless simulation script | MTTR (sim time), tool calls, confidence, memory hit rate, recommendation accuracy — plotted live |
| 17 | **Tooling** | `uv` (Python deps + lockfile), `pytest`, `ruff` · `npm` for frontend | Versions pinned by lockfiles at Phase 0 |

### Python dependencies (pin exact versions in the lockfile at Phase 0)

`fastapi`, `uvicorn[standard]`, `sse-starlette`, `langgraph`, `openai`, `langfuse`, `hindsight-client`, `pydantic`, `pydantic-settings`, `httpx`, `pytest`, `ruff`

### Frontend dependencies

`next`, `react`, `tailwindcss`, shadcn/ui components (added via CLI), `recharts`, `lucide-react`

---

## Final Architecture

```
┌────────────────────────────────────────────────────────────┐
│                    NEXT.JS FRONTEND                         │
│   Dashboard + Incident panel │ Memory Browser │ Learning    │
│   (shadcn/ui + Recharts, one global SSE stream)             │
└───────────────┬─────────────────────────────▲──────────────┘
                │ REST                        │ SSE
                ▼                             │
┌────────────────────────────────────────────────────────────┐
│                       FASTAPI                                │
│                                                              │
│   ┌──────────────┐      ┌──────────────────────────────┐   │
│   │ SIMULATOR     │      │ AGENT (LangGraph graph)      │   │
│   │ sim clock     │◀────▶│ recall_hints → investigate → │   │
│   │ state machine │      │ search_memory → decide →     │   │
│   │ incident gen  │      │ act ⏸ → verify → learn       │   │
│   │ (SQLite)      │      │ (record_lesson retry loop)   │   │
│   └──────────────┘      └──────┬───────────────────────┘   │
│                                 │                            │
│                    ┌────────────┼────────────┐             │
│                    ▼            ▼            ▼             │
│            ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│            │ HINDSIGHT │  │  GROQ    │  │ LANGFUSE │       │
│            │ retain()  │  │ gpt-oss- │  │ traces   │       │
│            │ recall()  │  │ 120b →   │  │          │       │
│            │ mental    │  │ qwen3-32b│  │          │       │
│            │  model    │  │          │  │          │       │
│            └──────────┘  └──────────┘  └──────────┘       │
└────────────────────────────────────────────────────────────┘
```

---

## Repository Structure

See BUILD_PLAN.md Section 4 for the full tree. Top level:

```
.
├── BUILD_PLAN.md · TECH_STACK.md · README.md · .env.example · pyproject.toml
├── backend/     # FastAPI, agent/, simulator/, memory/, eval/
├── tests/       # simulator, guardrails, graph routing
├── frontend/    # Next.js: app/page.tsx, app/memory, app/learning
└── scripts/     # spikes, simulate, seed_history, reset_demo, demo_dryrun
```

---

## Setup Checklist (do these before writing any code)

```
□ Hindsight Cloud account (ui.hindsight.vectorize.io) + apply MEMHACK99 in Billing
   → create API key (banks are created by the app at startup)
□ Groq account → API key (console.groq.com)
□ Langfuse Cloud account → project → public + secret keys (free tier)
□ Python 3.11+, uv, Node.js 20+ installed locally
```

**Env vars** (full list in BUILD_PLAN.md Section 15):
```bash
HINDSIGHT_BASE_URL=https://api.hindsight.vectorize.io
HINDSIGHT_API_KEY=...
HINDSIGHT_BANK_LIVE=memoryops-live
HINDSIGHT_BANK_SEEDED=memoryops-seeded
GROQ_API_KEY=...
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_HOST=https://cloud.langfuse.com
```

---

## Decision Log (for your README + judges + content deliverables)

Keep this — you'll need it for the *"Explanation of how Hindsight memory is used"* submission requirement and it's gold for your article.

| Decision | Why (one-liner for judges) |
|---|---|
| Single agent | Memory learning is clearest with one protagonist and one memory bank — the agent IS the story |
| LangGraph over LangChain/DeepAgents | Custom investigation loop needed full control; harness abstractions would bury the Hindsight calls |
| `hindsight-client` directly, not `hindsight-langgraph` nodes | The prebuilt nodes assume chat `MessagesState`; our state is structured incident data, and we want every retain/recall explicit and visible |
| Hindsight as the ONLY memory | Hackathon requirement, but also: 4-way retrieval (semantic + keyword + graph + temporal, reranked) beats building our own; observation consolidation gives free "learning from failure" |
| Memory access is deterministic, not an LLM tool | Two fixed touchpoints (before and after investigation) make memory visible in every trace and make the Memory ON/OFF comparison airtight |
| Two recall touchpoints | Hints before investigation make the agent *faster* (fewer tool calls); matches after investigation make it *more accurate* (right first action) |
| Episodes retained as prose + metadata, `document_id = incident_id` | Hindsight extracts facts from text; metadata lets us group recalled facts back into "matched incident INC-xxx"; document IDs make retains idempotent |
| "Incident Patterns" mental model | Hindsight writes and maintains the runbook itself from consolidated observations — visible proof the agent is building expertise, not just storing logs |
| Learn from environment consequences, not human corrections | The official cookbook demos (ClaimsIQ, CableConnect) learn from a human saying "wrong"; ours learns from a trap fix wearing off — our main innovation angle |
| Synchronous `retain` | The next incident may arrive a minute later on stage — memory must be recallable immediately |
| Memory match threshold calibrated, not assumed | Hindsight scores are relative per query; the threshold is set from measured score distributions in the Phase 0.5 spike |
| Simulated clock | MTTR is measured from real agent behavior (tool calls, trap fixes, retries) on a fast-forwarded clock — no invented numbers |
| `verify` step after every action | A trap fix looks like success for a minute; only watching recovery hold proves the fix, and catching the failure is what produces the lesson |
| LangGraph `interrupt()` for approval | Least code for pausing a graph mid-run and resuming it from an HTTP call |
| No vector DB | Hindsight IS the vector store — adding ChromaDB would be redundant architecture |
| SQLite | 5 machines don't need Postgres; zero-setup, single-file, judges can open it |
| Groq via OpenAI SDK | Recommended by organizers; fast enough for live traces; the OpenAI-compatible endpoint makes Langfuse tracing a one-line import |
| Langfuse | Open-source observability; traces prove the agent's reasoning is real, not hardcoded |
| Ops dashboard, not chat | The judge creates incidents in a simulator — this is a closed-loop system, not a chatbot |
| One global SSE stream | Opened once at page load: no subscribe race, trivial reconnect with replay |

---

## Reference Repositories (reviewed for v1.2)

What each suggested repo gives us — borrow **patterns**, not code dependencies. Nothing here changes the frozen stack.

### Adopted as pattern sources

| Repository | What we borrow | Where it lands |
|---|---|---|
| `vectorize-io/hindsight` | Python client signatures (`retain`, `recall`, `create_bank`, `create_mental_model`, `delete_bank`); recall `scores` object; sync retain; Groq provider's `tool_use_failed` salvage and `include_reasoning: false` | `memory/hindsight.py`, `llm.py` |
| `vectorize-io/hindsight-cookbook` → `applications/cable-co` | **Closest template**: `hindsight-client` + Hindsight Cloud + FastAPI; retain immediately on a failure *and* a full summary at the end (`document_id` per scenario); reset = delete + recreate bank; idempotent mental-model creation; Memory ON/OFF mode; `article.md` is a model for our "How Hindsight memory is used" write-up | `memory/`, `/api/admin/reset`, README |
| `vectorize-io/hindsight-cookbook` → `applications/claims-iq` | "Rookie → expert" framing; ground-truth validation of each decision; memory modes (no memory / recall / reflect / mental models) compared side by side | Differentiation note, `simulate.py` stretch |
| `vectorize-io/hindsight-cookbook` → `applications/deliveryman-demo` | Benchmark runner: one throwaway bank per config, parallel runs, JSON + markdown summary, "path efficiency" (actual vs optimal steps) | `scripts/simulate.py`, `investigation_efficiency` metric |
| `vectorize-io/self-driving-agents` → `engineering/ops/bank-template.json` | Vectorize's own incident-response bank config: `retain_mission`, `observations_mission`, "Incident Patterns" mental model with `refresh_after_consolidation` trigger | Bank setup (BUILD_PLAN 6.1, 6.5) |
| `vectorize-io/self-driving-agents` → `engineering/ops/incident-response-commander.md` | Severity matrix, runbook and post-mortem vocabulary for realistic data and episode wording | Incident templates, episode record |
| `JoshuaC215/agent-service-toolkit` | Interrupt/resume: `aget_state()` → if interrupted, `Command(resume=...)`; `astream(stream_mode=["updates","custom"])` for node events | `agent/nodes/act.py`, `main.py` |
| `swapnildahiphale/OpenSRE` | A "memory-first AI SRE" validating our concept; its episode schema (skills used, key findings, effectiveness, root cause) and per-issue-type "strategy" (recommended steps + anti-patterns) → our `INVESTIGATION PATH` / `DECISIVE EVIDENCE` and the Incident Patterns mental model | Episode record, README positioning |
| `wassim249/fastapi-langgraph-agent-production-ready-template` | LLM service pattern only: model fallback, exponential backoff, **total timeout budget** per call | `llm.py` |

### Reviewed and not used

| Repository | Why not |
|---|---|
| `vectorize-io/hindsight-skills` | Skills that teach coding agents about Hindsight; useful while *we* code (optional), not part of the product |
| `hindsight-langgraph` (in `vectorize-io/hindsight`) | Nodes assume chat `MessagesState`; our state is structured incident data |
| `langchain-ai/memory-agent` | Uses LangGraph's own store as memory — would be a second memory system |
| `wassim249/...` (rest of it) | mem0 + pgvector (second memory), Postgres, JWT, Prometheus/Grafana — out of scope |
| `Tracer-Cloud/opensre` | Cloud-infra SRE CLI and benchmark aimed at real integrations (Grafana, Opsgenie…); nothing reusable for a factory simulator beyond general alert-shape inspiration |
| `red-hat-data-services/agentic-starter-kits` | OpenShift/RHOAI deployment kits; we run locally for the demo |
| `OpenHands/OpenHands` | Coding-agent platform; not our domain |

---

## What's Deliberately NOT in the Stack

Multi-agent orchestration · MCP/A2A/ACP protocols · DeepAgents harness · `hindsight-langgraph` prebuilt nodes · Mem0/Zep/Cognee/second memory · ChromaDB/FAISS · Semantic layer · WrenAI/GenBI · Free-text incident input · LLM-callable memory tool · Redis/queues · Docker (optional only, if time allows)

Every one of these was considered and rejected for a reason. If anyone (teammate, judge, well-meaning senior) asks "why didn't you use X?" — the decision log above is your answer.
