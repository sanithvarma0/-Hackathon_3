# 🔒 TECH STACK — FROZEN

All decisions are locked. No more changes. Here's the definitive document — save this as your reference for the rest of the hackathon.

---

## The Stack

| # | Layer | Choice | Version/Detail |
|---|---|---|---|
| 1 | **Agent Runtime** | LangGraph (raw, no wrapper) | Single agent, custom graph, 5 nodes |
| 2 | **Memory** | Hindsight Cloud | Single memory bank · promo `MEMHACK99` ($50 credits) |
| 3 | **Decision Logic** | LangGraph conditional edges | No external routing framework |
| 4 | **Harness** | Custom-built (~150 lines) | Reference: `agent-service-toolkit` (JoshuaC215) |
| 5 | **Protocol** | None — FastAPI + SSE | (Hindsight's native LangGraph integration for memory calls) |
| 6 | **Observability** | Langfuse Cloud (free tier) | Traces LLM + tool calls |
| 7 | **Database** | SQLite | Simulator state only |
| 8 | **Vector Store** | None | Hindsight is the vector store |
| 9 | **Semantic Layer** | None | Hindsight TEMPR handles retrieval |
| 10 | **LLM** | Groq | Primary: `openai/gpt-oss-120b` · Fallback: `qwen/qwen3-32b` |
| 11 | **Backend** | FastAPI | Python 3.11+, SSE streaming |
| 12 | **Frontend** | Next.js + Tailwind + shadcn/ui + Recharts | Ops dashboard, not a chat |
| 13 | **Guardrails** | 6 custom rules | Pydantic validation, action whitelist, iteration cap, LLM retry, state validation, similarity threshold |
| 14 | **Evaluation** | Custom tracker | MTTR, confidence, memory hit rate — plotted live |

---

## Final Architecture

```
┌────────────────────────────────────────────────────────────┐
│                    NEXT.JS FRONTEND                         │
│   Dashboard │ Incident View │ Memory Browser │ Learning     │
│   (shadcn/ui + Recharts, SSE-fed)                           │
└───────────────────────────┬────────────────────────────────┘
                            │ SSE stream
                            ▼
┌────────────────────────────────────────────────────────────┐
│                       FASTAPI                                │
│                                                              │
│   ┌──────────────┐      ┌──────────────────────────────┐  │
│   │ SIMULATOR     │      │ AGENT (LangGraph graph)      │  │
│   │ state machine │◀────▶│                               │  │
│   │ + incident    │      │  investigate → search_memory  │  │
│   │   generator   │      │       → decide → act → learn  │  │
│   │ (SQLite)      │      │       (retry loop on fail)    │  │
│   └──────────────┘      └──────┬───────────────────────┘  │
│                                 │                            │
│                    ┌────────────┼────────────┐             │
│                    ▼            ▼            ▼             │
│            ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│            │ HINDSIGHT │  │  GROQ    │  │ LANGFUSE │       │
│            │ retain()  │  │ gpt-oss- │  │ traces   │       │
│            │ recall()  │  │ 120b →   │  │          │       │
│            │ reflect() │  │ qwen3-32b│  │          │       │
│            └──────────┘  └──────────┘  └──────────┘       │
└────────────────────────────────────────────────────────────┘
```

---

## Repository Structure

```
memoryops/
├── backend/
│   ├── main.py                 # FastAPI app, routes, SSE
│   ├── agent/
│   │   ├── graph.py            # LangGraph definition (5 nodes)
│   │   ├── state.py            # AgentState schema
│   │   ├── nodes/
│   │   │   ├── investigate.py  # LLM tool-calling loop
│   │   │   ├── memory.py       # Hindsight recall wrapper
│   │   │   ├── decide.py       # Recommendation node
│   │   │   ├── act.py          # Execute judge's action
│   │   │   └── learn.py        # Write outcome to Hindsight
│   │   ├── tools.py            # Tool registry (get_metrics,
│   │   │                       #   get_events, get_logs, 
│   │   │                       #   search_memory)
│   │   └── prompts.py          # System prompts per node
│   ├── simulator/
│   │   ├── engine.py           # State machine + degradation
│   │   ├── incidents.py        # Incident templates + noise
│   │   └── db.py               # SQLite models
│   ├── guardrails.py           # The 6 guardrails
│   ├── llm.py                  # Groq client + retry/fallback
│   ├── eval/
│   │   └── metrics.py          # MTTR, confidence, hit-rate tracker
│   └── config.py               # Env vars, settings
├── frontend/
│   ├── app/
│   │   ├── page.tsx            # Dashboard
│   │   ├── incident/[id]/page.tsx
│   │   ├── memory/page.tsx     # Memory browser
│   │   └── learning/page.tsx   # Learning curves
│   ├── components/             # shadcn/ui components
│   └── lib/sse.ts              # SSE client
├── README.md
└── docker-compose.yml          # Optional: one-command run
```

---

## Setup Checklist (do these before writing any code)

```
□ Hindsight Cloud account + apply MEMHACK99 ($50 credits)
   → create memory bank, save API key
□ Groq account → API key (console.groq.com)
□ Langfuse Cloud account → API keys (free tier)
□ GitHub repo created (memoryops) — commit this stack doc first
```

**Env vars:**
```bash
HINDSIGHT_API_KEY=...
HINDSIGHT_BANK_ID=...
GROQ_API_KEY=...
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
```

---

## Decision Log (for your README + judges + content deliverables)

Keep this — you'll need it for the *"Explanation of how Hindsight memory is used"* submission requirement and it's gold for your article:

| Decision | Why (one-liner for judges) |
|---|---|
| Single agent | Memory learning is clearest with one protagonist and one memory bank — the agent IS the story |
| LangGraph over LangChain/DeepAgents | Custom investigation loop needed full control; harness abstractions would bury the Hindsight calls |
| Hindsight as the ONLY memory | Hackathon requirement, but also: TEMPR's 4-way retrieval (semantic+keyword+graph+temporal) beats building our own; observation consolidation gives free "learning from failure" |
| No vector DB | Hindsight IS the vector store — adding ChromaDB would be redundant architecture |
| SQLite | 5 machines don't need Postgres; zero-setup, single-file, judges can open it |
| Groq | Recommended by organizers; fast enough for live demo investigation traces |
| Langfuse | Open-source observability; traces prove the agent's reasoning is real, not hardcoded |
| Ops dashboard, not chat | The judge creates incidents in a simulator — this is a closed-loop system, not a chatbot |

---

## What's Deliberately NOT in the Stack

Multi-agent orchestration · MCP/A2A/ACP protocols · DeepAgents harness · Mem0/Zep/Cognee/second memory · ChromaDB/FAISS · Semantic layer · WrenAI/GenBI · Free-text incident input

Every one of these was considered and rejected for a reason. If anyone (teammate, judge, well-meaning senior) asks "why didn't you use X?" — the decision log above is your answer.