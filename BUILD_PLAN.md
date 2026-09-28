# MemoryOps — Build Specification v1.8

> **v1.8 changes (LLM provider + spend tracking):** primary LLM is now **OpenAI `gpt-5.4-mini`** with Groq `gpt-oss-120b` as a cross-provider fallback (Groq's free tier is 8,000 tokens/min; OpenAI's quota for this key is 180M/min; Gemini returned 503 on every current model). Every LLM call and memory write is recorded in a persistent **usage ledger** with tokens and cost from the published price list, and a **hard spend cap** stops LLM calls (→ escalation) when reached (7.6). Escalations now close with the **on-call engineer's fix on the ticket**, so the agent learns from what humans did (5.5, 6.2). Measured OpenAI constraints and dry-run results in 14.1c.
>
> **v1.7 changes (Phase 2 built):** incident open/acknowledge/resolve moved to a separate `IncidentLifecycle` port (PagerDuty's role), so the agent never touches the simulator; the agent is async end to end (the Hindsight sync client binds to per-thread event loops); graph gains `request_approval` (human wait starts once, not on interrupt re-entry) and `escalate` nodes; live dry-run findings recorded in 14.1 (Groq free-tier token limit, pseudo-tool salvage, query paraphrasing fixes, memory's measured effect).
>
> **v1.6 changes (Phase 1 built):** `execute_action` returns an `ActionReceipt` (confirmation only) instead of the effect — returning the effect would leak ground truth to the agent; `verify` now infers the effect from a `RecoveryObservation`. Trap fixes recover to 90–94% (looks fixed) before re-degrading, instead of an obviously-partial 65–75%. Simulator API (5.8) updated to the implemented control plane.
>
> **v1.5 changes (UI direction):** Section 10 rewritten as the "industrial control room" UI spec — plant-floor SVG world view, right-rail panels, purple reserved for memory, per-incident visual signatures, SSE→UI mapping, designed states, timebox + fallback. Plant topology (5.2) changed to one serial line with a robotic material-handling cell and two network gateways, so the world view reads as a real line and network cascades are visible. Match badges show rank + strength, never raw similarity (per 14.1).
>
> **v1.4 changes (production-grade pass):** added the engineering principles that govern every claim (Section 1.1); the **environment adapter boundary** (5.9); a realistic **6-month seeded history** spec (6.6); Section 11 rewritten as a full **evaluation suite** (paired ON/OFF design, seeds, bootstrap CIs, transfer + discrimination + retrieval + calibration metrics, auto-generated report); **engineering quality** (tests, CI, mypy, ADRs, `make`, docker compose, designed error states — Section 17); **Q&A answers backed only by measured results** (Section 18); an explicit **priority stack and anti-scope list** (Section 19). Phases re-cut so evaluation and seeding are first-class (Section 13).
>
> **v1.3 changes (Phase 0.5 spikes run against the real services — Section 14):** Groq no longer serves `qwen/qwen3-32b` → fallback is `qwen/qwen3.8-27b`; semantic cosine does **not** separate true from false incident matches, so the 0.7 similarity threshold is replaced by a reranker-relative rule, the episode gains a generalized `SIGNATURE:` line, and recall queries are built from observed signals only; `tool_use_failed` payloads are usually refusal text, not salvageable tool calls; models may emit parallel tool calls and may silently coerce invalid arguments.
>
> **v1.2 changes (from v1.1, after reviewing reference repos — see TECH_STACK.md "Reference Repositories"):** "Incident Patterns" mental model promoted from stretch to core (Phase 3), configured from Vectorize's own ops bank template; episode record gains an `INVESTIGATION PATH` section (what evidence was decisive) to power `recall_hints`; Groq failure handling made concrete (`tool_use_failed` salvage, `include_reasoning: false`, fallback model verified at spike time); `simulate.py` runs each mode on its own throwaway bank and reports investigation efficiency; added a differentiation note against the official Hindsight cookbook demos.
>
> **v1.1 changes (from v1.0):** grounded all Hindsight calls in the real `hindsight-client` v0.10 SDK; added a Phase 0.5 spike; added a simulated clock so MTTR is measured, not invented; added a `verify` step (without it a trap fix looks like success); added a second memory touchpoint (`recall_hints`) so memory makes the agent *faster*, not just more accurate; removed the LLM-callable memory tool (it leaked memory into Memory-OFF runs); defined Memory-OFF semantics, demo reset, bank switching, SSE replay, Hindsight outage handling, full action/effect matrix, custom-builder classification rules, and a submission checklist. All headline numbers are now **targets to be replaced with measured values** (Section 14).

---

## 1. Project Overview

**MemoryOps** is a self-learning production incident commander. It simulates a factory floor (5 machines) where incidents occur. A single AI agent detects, investigates, and diagnoses incidents — and critically, **remembers every past incident via Hindsight**, so repeated incident classes get resolved faster and more accurately over time.

**The demo thesis:** the first incident of a class is slow and uncertain; later incidents of the same class — on a *different* machine, with *different* log wording — are fast, confident, and cite the earlier incident. Judges can trigger incidents themselves and verify the learning is real, not scripted.

> Target shape (placeholder until measured): incident #1 → ~10 sim-min MTTR, ~0.6 confidence; incident #5 of the same class → ~3 sim-min MTTR, ~0.9 confidence. Replaced with real numbers from the committed eval report (Section 11) — until then, no numbers are quoted anywhere.

**This is NOT a chatbot.** The judge interacts with a simulated production environment (buttons, sliders, dashboards). The agent runs autonomously in response to environmental events. The judge's "test" is: trigger an incident → watch the agent investigate → approve an action → trigger the same class again → watch it be faster.

**Fit to the problem statement:** "Incident Response Agent" (Engineering & DevOps category), applied to manufacturing / OT. Memory is the product: the same agent with memory OFF is measurably worse, live, on stage.

**Differentiation (Innovation = 30%).** Judges from Vectorize will know the official cookbook demos — ClaimsIQ (claims triage, "confused rookie → seasoned expert") and CableConnect (CSR copilot that learns from rejections). Both learn from a **human telling the agent it was wrong**. MemoryOps learns from **the environment's delayed consequences**: nobody tells the agent RESTART was wrong — the machine re-degrades 90 seconds later, the agent notices, and that becomes memory. Plus: judges inject incidents themselves into a live simulator (not a fixed scenario queue), the agent generalizes a signature across *different machines and wording*, and improvement is measured in an operational KPI (MTTR), not just "right/wrong". Say this explicitly in the README and demo.

### 1.1 Engineering Principles (apply to every phase)

Judges see three surfaces — **the repo, the demo, the Q&A**. "Production-grade" means production-grade on those surfaces:

| We invest in | We deliberately skip |
|---|---|
| Observability — Langfuse traces of every LLM + tool call, visible in the demo | Auth, login, multi-tenancy |
| Evaluation — measured with a reproducible script, never claimed | Microservices, Kubernetes, message queues |
| Error handling — graceful, and *visible* as designed UI states | Postgres / Redis (SQLite is enough) |
| Tests + CI — green badge on the README | Real Datadog/K8s integrations (the adapter interface is enough) |
| Realistic data — machines, logs, operators, 6-month history | More incident types, a second agent, a second memory |
| Reproducibility — one command to run, one command to evaluate | Deployment beyond docker compose |
| Documentation — README, ADRs, auto OpenAPI docs | |

**The number rule:** every number that appears in the README, the demo, the article or the Q&A is produced by a script in this repo, and the report that produced it is committed. No estimated or aspirational numbers. Where something has not been measured, we say so.

---

## 2. Tech Stack (FROZEN — see TECH_STACK.md for versions and rationale)

| Layer | Choice | Notes |
|---|---|---|
| Agent runtime | **LangGraph** (raw `StateGraph`) | Single agent. No `create_react_agent`, no DeepAgents, no multi-agent. |
| Human-in-the-loop | **LangGraph `interrupt()` + `InMemorySaver`** | `thread_id = incident_id`; resume with `Command(resume=...)`. |
| Memory | **Hindsight Cloud** via `hindsight-client` (Python, v0.10.x) | `https://api.hindsight.vectorize.io`. The ONLY memory system. Promo `MEMHACK99`. |
| LLM | **OpenAI** primary, **Groq** fallback (both via the OpenAI SDK) | `gpt-5.4-mini` (`reasoning_effort=none`, `temperature=0`, fixed seed) → Groq `openai/gpt-oss-120b`. Every call recorded in the usage ledger (7.6). |
| Backend | **FastAPI** + `sse-starlette` | Python 3.11+. |
| Database | **SQLite** | Simulator state, metrics, episode outbox. No vector DB. |
| Observability | **Langfuse** Cloud (free tier) | `langfuse.openai` drop-in + `@observe` on graph nodes. |
| Frontend | **Next.js (App Router) + Tailwind + shadcn/ui + Recharts** | Ops dashboard. No chat UI. |
| Guardrails | Custom (6 rules, Section 8) | Pydantic v2 validation everywhere. |

**Explicitly rejected** (do not add): multi-agent orchestration, MCP/A2A/ACP, DeepAgents, Mem0/Zep/Cognee, any second memory, any vector DB, semantic layer, WrenAI/GenBI, free-text incident input, `hindsight-langgraph` prebuilt nodes (they assume `MessagesState`; our state is custom).

**Reference implementations** (full list and what we borrow from each: TECH_STACK.md → "Reference Repositories"):
- Hindsight Python client + API docs: https://github.com/vectorize-io/hindsight (`hindsight-clients/python`, `hindsight-docs`)
- Closest Hindsight app patterns: `vectorize-io/hindsight-cookbook` → `applications/cable-co` (hindsight-client + Cloud + FastAPI, retain-on-failure + retain-at-end, reset by delete/recreate bank, mental models) and `applications/claims-iq` (memory-mode comparison, ground-truth validation)
- Incident bank config: `vectorize-io/self-driving-agents` → `engineering/ops/bank-template.json`
- FastAPI + LangGraph interrupt/resume + streaming: `JoshuaC215/agent-service-toolkit` (`src/service/service.py`)

---

## 3. System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    NEXT.JS FRONTEND                          │
│  Dashboard + Incident panel │ Memory Browser │ Learning      │
│  [🧠 Memory ON/OFF toggle — prominent, in header]            │
└──────────────┬──────────────────────────────▲───────────────┘
               │ REST (trigger, action, admin)│ SSE (one global stream)
               ▼                              │
┌─────────────────────────────────────────────────────────────┐
│                        FASTAPI                               │
│                                                              │
│  ┌──────────────────┐     ┌────────────────────────────────┐│
│  │ SIMULATOR        │     │ AGENT (LangGraph)              ││
│  │ sim clock        │◀───▶│ recall_hints → investigate →   ││
│  │ state machine    │     │ search_memory → decide →       ││
│  │ incident gen     │     │ act ⏸ → verify → learn         ││
│  │ (SQLite)         │     │ (retry loop via record_lesson) ││
│  └──────────────────┘     └───────┬────────────────────────┘│
│  ┌──────────────────┐             │                          │
│  │ EVENT BUS        │◀────────────┤  (every node emits events)│
│  │ ring buffer +    │             │                          │
│  │ SSE fan-out      │   ┌─────────┼─────────────┐            │
│  └──────────────────┘   ▼         ▼             ▼            │
│                   ┌──────────┐ ┌──────────┐ ┌──────────┐     │
│                   │HINDSIGHT │ │  GROQ    │ │ LANGFUSE │     │
│                   │retain    │ │gpt-oss-  │ │ traces   │     │
│                   │recall    │ │120b →    │ │          │     │
│                   │mental    │ │qwen3.8   │ │          │     │
│                   │ model    │ └──────────┘ └──────────┘     │
│                   └──────────┘                               │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Repository Structure

The repo root **is** the project (no extra `memoryops/` folder).

```
.
├── BUILD_PLAN.md               # this document
├── TECH_STACK.md               # frozen stack + decision log
├── README.md                   # final deliverable: diagram, eval table, CI badge
├── Makefile                    # setup | dev | test | lint | typecheck | eval | seed | demo
├── docker-compose.yml          # backend + frontend, one command
├── .env.example
├── pyproject.toml              # backend deps (pinned via uv.lock)
├── .github/workflows/ci.yml    # ruff, mypy, pytest, frontend lint + build (no secrets)
├── docs/
│   ├── adr/                    # 6 Architecture Decision Records (Section 17.3)
│   └── eval/                   # committed eval reports: REPORT.md, charts, results.json
├── fixtures/
│   └── seed_history.jsonl      # generated once, committed; seeding replays it (6.6)
├── backend/
│   ├── main.py                 # FastAPI app, routes, SSE endpoint
│   ├── config.py               # pydantic-settings: env vars, SIM_SPEED, thresholds
│   ├── events.py               # event bus: ring buffer, SSE fan-out, Last-Event-ID replay
│   ├── llm.py                  # Groq client (OpenAI SDK) + retry + fallback + Langfuse
│   ├── guardrails.py           # the 6 guardrails
│   ├── schemas.py              # Pydantic contracts: metrics, events, ActionReceipt, incidents, API payloads
│   ├── adapters/
│   │   ├── base.py             # EnvironmentAdapter Protocol — the agent's only view (5.9)
│   │   ├── simulator.py        # implementation backed by the simulator
│   │   └── datadog.py          # stub: raises NotImplementedError, documents the mapping
│   ├── agent/
│   │   ├── graph.py            # StateGraph definition + routing functions
│   │   ├── state.py            # AgentState TypedDict
│   │   ├── nodes/
│   │   │   ├── recall_hints.py # memory touchpoint #1 (alert-based)
│   │   │   ├── investigate.py  # LLM tool-calling loop
│   │   │   ├── search_memory.py# memory touchpoint #2 (evidence-based)
│   │   │   ├── decide.py       # LLM → validated Recommendation
│   │   │   ├── act.py          # interrupt() for human approval, executes action
│   │   │   ├── verify.py       # watch metrics for VERIFY_WINDOW; detect re-degradation
│   │   │   ├── record_lesson.py# retain partial lesson, loop back to investigate
│   │   │   └── learn.py        # retain final episode, metrics, close incident
│   │   ├── tools.py            # the 4 tools the LLM sees
│   │   └── prompts.py          # all system prompts
│   ├── simulator/
│   │   ├── clock.py            # simulated clock (SIM_SPEED sim-seconds per real second)
│   │   ├── engine.py           # state machine, degradation, recovery, re-degradation
│   │   ├── incidents.py        # templates, noise, effects matrix, custom classifier
│   │   ├── machines.py         # machine profiles, line/gateway topology
│   │   └── db.py               # SQLite schema + init + 7-day history backfill
│   ├── memory/
│   │   ├── hindsight.py        # client wrapper: ensure_bank, retain_episode, recall_*
│   │   ├── render.py           # episode/lesson → prose templates; query paraphraser
│   │   └── outbox.py           # retry retains that failed (Hindsight outage)
│   └── eval/
│       ├── metrics.py          # per-incident metrics tracker (live + eval)
│       ├── battery.py          # scenario battery: variants × noise × seeds (11.2)
│       ├── harness.py          # headless runner: fast-forward clock, auto-approve, resume
│       ├── stats.py            # bootstrap CIs, paired differences, Brier score
│       └── report.py           # REPORT.md + charts + results.json
├── tests/
│   ├── test_simulator.py       # effects matrix, re-degradation, classifier, clock
│   ├── test_adapter.py         # simulator adapter satisfies the Protocol contract
│   ├── test_guardrails.py      # whitelist, schema, tool-call cap, arg validation
│   ├── test_schemas.py         # Pydantic models, API payloads
│   ├── test_memory_render.py   # episode/lesson/query rendering, match rule
│   ├── test_llm.py             # retry, fallback, tool_use_failed handling (fake client)
│   ├── test_graph_routing.py   # routing with fake LLM + fake memory
│   ├── test_eval_stats.py      # bootstrap / Brier on known inputs
│   └── test_health.py
├── frontend/
│   ├── app/
│   │   ├── page.tsx            # Dashboard + live incident panel
│   │   ├── memory/page.tsx     # Memory Browser
│   │   └── learning/page.tsx   # Learning curves
│   ├── components/             # shadcn/ui + custom (MachineCard, TraceLog, ...)
│   └── lib/
│       ├── api.ts              # REST client
│       └── sse.ts              # EventSource client w/ reconnect
└── scripts/
    ├── spike_hindsight.py      # Phase 0.5: measure recall scores + latency
    ├── spike_groq.py           # Phase 0.5: tool-calling + error shapes on both models
    ├── spike_recall_design.py  # Phase 0.5: record/query design vs match separation
    ├── run_eval.py             # `make eval`: evaluation suite → docs/eval/ (Section 11)
    ├── generate_history.py     # one-off: build fixtures/seed_history.jsonl (6.6)
    ├── seed_history.py         # `make seed`: replay the fixture into the seeded bank
    ├── reset_demo.py           # wipe SQLite + recreate live bank
    └── demo_dryrun.py          # scripted end-to-end run of Section 12
```

---

## 5. Simulator Design (Phase 1)

### 5.1 Simulated Clock

All simulator dynamics and MTTR use **sim time**. `SIM_SPEED` (default `10`) = sim-seconds per real second, configurable in `.env`. At 10×, a 60 sim-second re-degradation happens ~6 s later on stage.

- The world clock always runs (dashboard jitter, degradation, re-degradation).
- **MTTR = sim time from incident onset → verified full recovery, excluding time the incident spends in `awaiting_action`** (human latency is not the agent's fault). Human wait is recorded separately.
- Agent wall-clock time (real seconds of LLM + tool work) is recorded separately too.

### 5.2 Machines & Topology

One serial production line with a robotic material-handling cell, on two network gateways:

```
            GW-A (M1, M2, M5)                 GW-B (M3, M4)
FEED ──▶ M1 ──▶ M2 ──▶ M3 ──▶ M4 ──▶ SHIP
                  ▲             ▲
                  └──── M5 ─────┘      (M5 loads/unloads M2 and M4)
```

| ID | Profile | Role | Gateway |
|---|---|---|---|
| M1 | CNC vertical mill — Haas VF-4, commissioned 2021 | line stage 1 | GW-A |
| M2 | CNC lathe — Mazak QT-250, commissioned 2019 | line stage 2 | GW-A |
| M3 | Robotic welding cell — Fanuc ARC Mate 100iD, commissioned 2022 | line stage 3 | GW-B |
| M4 | 5-axis machining center — DMG Mori NVX 5080, commissioned 2020 | line stage 4 | GW-B |
| M5 | Robotic material-handling cell — ABB IRB 6700 with vision-guided gripper, commissioned 2022 | loads M2 and M4 | GW-A |

- **Downstream** = later stages on the line (M1 → M2 → M3 → M4), plus M2 and M4 for M5.
- **Network failures** hit every machine on the affected gateway at once (GW-A: 3 machines, GW-B: 2), then starve downstream stages.
- **Starvation is a line-level effect, not a machine fault:** a degraded stage caps the *line* throughput KPI and dims flow animation downstream, but downstream machines' own health metrics stay normal. So the agent's tools never see a starving machine as a second incident, and the UI still shows the ripple.

### 5.3 State Model (SQLite)

```sql
machines(id TEXT PRIMARY KEY, name TEXT, profile TEXT, line TEXT, gateway TEXT,
         status TEXT,                    -- healthy | degraded | critical
         throughput REAL, oee REAL, error_rate REAL,
         temperature_c REAL, sensor_variance REAL,
         packet_loss_pct REAL, latency_ms REAL, memory_pct REAL,
         config_version TEXT, last_calibration_ts INTEGER)

machine_history(ts INTEGER, machine_id TEXT,
                throughput REAL, oee REAL, error_rate REAL, temperature_c REAL,
                sensor_variance REAL, packet_loss_pct REAL, latency_ms REAL, memory_pct REAL)
                -- backfilled 7 days @ 15-min granularity at init; 1 row / 10 sim-s live

events(ts INTEGER, machine_id TEXT,
       event_type TEXT,                 -- config_deployed | calibration | gateway_restart
       detail TEXT)                     --  | oom_kill | cache_cleared | machine_restart ...

logs(ts INTEGER, machine_id TEXT, level TEXT, source TEXT, message TEXT)

incidents(id TEXT PRIMARY KEY,          -- "INC-001"
          type TEXT,                    -- ground truth; NEVER shown to the agent
          machine_id TEXT, signature TEXT,   -- JSON
          status TEXT,                  -- open | awaiting_action | verifying | resolved | escalated
          memory_enabled INTEGER,
          onset_ts INTEGER, detected_ts INTEGER, resolved_ts INTEGER,
          human_wait_sim_s INTEGER, resolution_action TEXT, mttr_sim_s INTEGER)

actions_log(ts INTEGER, incident_id TEXT, attempt INTEGER, action TEXT,
            effect TEXT,                -- full_recovery | partial_recovery | no_effect | escalated
            recovery_pct REAL, re_degraded_after_sim_s INTEGER NULL,
            executed_by TEXT)           -- judge | auto

episodes(document_id TEXT PRIMARY KEY,  -- "INC-001" or "INC-001:lesson-1"
         incident_id TEXT, kind TEXT,   -- episode | lesson
         text TEXT,                     -- exact prose retained to Hindsight
         created_ts INTEGER,
         retain_status TEXT)            -- pending | retained | failed (outbox)

metrics(...)                            -- see Section 11
```

**Healthy baseline:** throughput 95–99%, OEE 88–93%, error_rate 0–2%, temperature 38–46 °C, sensor_variance 0.01–0.05, packet_loss < 0.5%, latency 2–8 ms, memory 35–55%. Slow ambient random walk so the dashboard looks alive. Seed realistic events into the 7-day backfill (routine config deploys, calibrations every 14–28 days, one gateway restart) so events are *not* a giveaway on their own.

### 5.4 Incident Taxonomy

Each incident type = **stable signature + noisy surface**. Config Regression and Sensor Drift are the polished hero scenarios; build those end-to-end first.

| Type | True Signal (signature) | Surface Noise (varies every occurrence) | Correct Fix |
|---|---|---|---|
| `config_regression` | config deployed 5–20 sim-min before onset; **gradual** throughput decline 25–45% | machine, drop %, log phrasing variant, onset delay, config version strings | `ROLLBACK_CONFIG` |
| `sensor_drift` | calibration age > 30 days; sensor variance high; config **unchanged** | machine, drift magnitude, phantom temperature alarms | `RECALIBRATE_SENSOR` |
| `network_failure` | packet loss 8–15%, latency spikes; **sudden** onset on all machines of one gateway | which gateway (GW-A: M1, M2, M5 / GW-B: M3, M4), loss %, latency | `RESTART_GATEWAY` |
| `resource_exhaustion` | memory climbing over 3+ days in history; OOM-kill events | machine, climb rate | `CLEAR_CACHE` |

**Action whitelist (exhaustive):** `ROLLBACK_CONFIG`, `RESTART_MACHINE`, `RECALIBRATE_SENSOR`, `RESTART_GATEWAY`, `CLEAR_CACHE`, `ESCALATE_HUMAN`.

### 5.5 Effects Matrix (complete — `execute_action` implements exactly this)

| Type ↓ / Action → | ROLLBACK_CONFIG | RESTART_MACHINE | RECALIBRATE_SENSOR | RESTART_GATEWAY | CLEAR_CACHE |
|---|---|---|---|---|---|
| `config_regression` | **full** | temporary relief → 90–94% (**looks fixed**), **re-degrades after 60–120 sim-s** | none | none | none |
| `sensor_drift` | none (config never wrong) | none | **full** | none | none |
| `network_failure` | none | none (machine fine, network broken) | none | **full** (all affected machines) | none |
| `resource_exhaustion` | none | temporary relief → 90–95% (**looks fixed**), **re-degrades after 120–150 sim-s** | none | none | **full** |

- `ESCALATE_HUMAN` (any type): incident closed as `escalated`; a fixed `ESCALATION_PENALTY_SIM_S` (default 1800) is added to MTTR to reflect handing off to an on-call engineer. The engineer fixes it and notes the fix on the ticket (`engineer_action` = the class's correct fix; for `ambiguous`, a repair outside the action set). `IncidentLifecycle.resolve` returns that note, and the episode records it — so the agent learns from what the human did, not only from its own attempts ("Day 1: escalated, 30 min. Day 2: remembers the engineer's fix, 5 min.").
- `none` = metrics unchanged; the incident keeps degrading.
- Re-degradation windows are deliberately shorter than `VERIFY_WINDOW_SIM_S` (Section 7.2) so `verify` always catches a trap fix.

### 5.6 Incident Generation

```python
INCIDENT_TEMPLATES = {
  "config_regression": {
     "signature":  {"config_changed_before_min": (5, 20),
                    "onset": "gradual", "throughput_drop_pct": (25, 45)},
     "log_variants": [
        "servo timeout on axis {axis}",
        "cycle time +{pct}% (nominal exceeded)",
        "control loop jitter, position error {err}mm",
        "feed override clamped at {pct}% by controller",
        "spindle load oscillation ±{pct}% after parameter reload"],
  }, ...   # >= 5 variants per type so repeats never share wording
}

def generate_incident(type, machine=None, custom=None) -> Incident:
    """1. Pick machine (random HEALTHY one if not given).
       2. Sample signature params within ranges.
       3. Sample noise: log phrasing variants, drop %, timestamps.
       4. Write preconditions into history/events (e.g. the config deploy 5–20 sim-min ago,
          the 3-day memory climb) so the agent's tools can find them.
       5. Write incident row; start degradation."""
```

**Degradation dynamics:** gradual types step down over 300–600 sim-s (30–60 s real at 10×). `network_failure` is instant. **Detection:** the incident is "detected" when throughput crosses the alert threshold (< 90%); the agent run starts then. `onset_ts` is when degradation began.

**IGNORE:** the `/ignore` endpoint applies the escalation immediately (the affected machine(s) drop a further 10–20% and go critical; line throughput falls further, so downstream flow visibly starves — 5.2) and the incident stays open awaiting action. No automatic 8-minute timer in demo mode — a judge reading the screen should never trigger a cascade by accident. (Optional `AUTO_CASCADE_SIM_S` for non-demo runs.)

### 5.7 Custom Incident Builder (structured, no free text)

Input: `{machine, config_changed: bool, minutes_before: 1..60, throughput_delta: -50..-5, error_rate: 0..20, temperature: normal|high, calibration: fresh|old, network: normal|degraded, memory_trend: flat|climbing}`.

Classification (first match wins; the agent never sees the result):
1. `network == degraded` → `network_failure`
2. `config_changed and 5 <= minutes_before <= 30` → `config_regression`
3. `calibration == old and not config_changed` → `sensor_drift`
4. `memory_trend == climbing` → `resource_exhaustion`
5. otherwise → `ambiguous`: no fix works; the only correct action is `ESCALATE_HUMAN` (tests the agent's willingness to say "I don't know").

### 5.8 Simulator API (control plane for FastAPI + eval harness; read/act side exposed to the agent only via the adapter, 5.9)

```python
class Simulator:                                         # backend/simulator/engine.py
    # agent-facing (exposed only through SimulatorAdapter, 5.9)
    def get_machine_metrics(machine_id) -> MachineMetrics
    def get_metric_history(machine_id, metric, window_hours) -> list[MetricPoint]  # <= 60 points
    def get_recent_events(machine_id, window_minutes) -> list[Event]   # incl. its gateway
    def get_error_logs(machine_id, window_minutes) -> list[str]        # WARN and above
    def execute_action(incident_id, action) -> ActionReceipt           # confirmation only
    def observe_recovery(incident_id, window_sim_s) -> RecoveryObservation
    # control plane (API, eval harness, dashboard)
    def trigger_incident(type=None, machine=None, custom=None) -> Incident
    def begin_wait(incident_id)          # recommendation shown; human wait excluded from MTTR
    def close_incident(incident_id) -> Incident   # after verify held
    def ignore(incident_id)              # escalation consequence
    def plant_state() -> PlantState      # dashboard: machines, gateways, starvation, KPIs
    def action_log(incident_id) -> list[ActionRecord]  # GROUND TRUTH: eval + Memory Browser only
    def subscribe(listener)              # incident_detected | re_degradation | incident_closed
    def tick(); def reset()
```

- **No ground truth crosses the adapter.** `execute_action` returns what a real ops API returns — a receipt ("M3 controller restarted"). The true effect is recorded in `actions_log` for evaluation; the agent learns whether a fix worked only by observing recovery (`verify`).
- Every metric is a closed-form function of sim time plus a seeded random walk on a fixed 10 s grid, so the same seed gives the same world at any clock speed (demo realtime, test manual, eval fast-forward).
- Reads advance the world lazily (`tick()` first); the API's background loop only exists to push SSE updates.

### 5.9 Environment Adapter Boundary (the agent never imports the simulator)

The agent sees the world only through this Protocol. The simulator implements it; a real deployment implements it with observability/ops clients. The agent core, memory loop and learning are unchanged.

```python
class EnvironmentAdapter(Protocol):
    # read side — backs the 4 LLM tools (7.3)
    def get_machine_metrics(self, machine_id: str) -> MachineMetrics: ...
    def get_metric_history(self, machine_id: str, metric: str, window_hours: int) -> list[MetricPoint]: ...
    def get_recent_events(self, machine_id: str, window_minutes: int) -> list[Event]: ...
    def get_error_logs(self, machine_id: str, window_minutes: int) -> list[str]: ...
    # write side — used only by the act node, after human approval
    def execute_action(self, incident_id: str, action: Action) -> ActionResult: ...
    # used by verify
    def observe_recovery(self, incident_id: str, window_sim_s: int) -> RecoveryObservation: ...
```

- **Not in the adapter:** `trigger_incident`, `ignore`, `tick`, `reset` — they are the simulator's control plane (a real factory has no "trigger incident" API). FastAPI and the eval harness call them on the simulator directly.
- **`IncidentLifecycle` port** (`get_alert`, `acknowledge`, `resolve`): the ticket side — what PagerDuty/Opsgenie does in production. Separate from the adapter because it is about the incident record, not the machines. The alert carries symptoms only (machine, throughput vs nominal, which machines are alerting), never a diagnosis.
- All return types are Pydantic models in `backend/schemas.py`, so the contract is typed and tested (`tests/test_adapter.py`).
- `backend/adapters/datadog.py` is a stub whose docstring maps each method to the real source (metrics → Datadog metrics query, events → deploy/change events, logs → log search, actions → runbook automation / PagerDuty). Every method raises `NotImplementedError`. Visible intent, zero risk.
- Q&A line: *"The agent only sees six typed methods. This demo implements them with a deterministic simulator; in production you implement them with Datadog, PagerDuty and your orchestrator. Nothing else changes."*

---

## 6. Hindsight Memory Layer (Phase 2)

### 6.1 Banks

| Bank ID | Purpose |
|---|---|
| `memoryops-live` | Starts empty. Used for the main demo so the first incident is genuinely cold. |
| `memoryops-seeded` | Pre-loaded by `seed_history.py` with 6 months of realistic history (~48 episodes, 6.6). Behind the **[LOAD 6-MONTH OPS HISTORY]** button and the "recalls something from weeks ago" beat. |
| `memoryops-eval-<run>-<cond>-<seed>` | Throwaway banks created and deleted by `run_eval.py` (Section 11). Never share state with the demo banks. |

Active bank is selected via `POST /api/admin/reset`. Bank creation (idempotent, at startup):

```python
client.create_bank(
    bank_id=bank_id,
    name="MemoryOps incident memory",
    mission="I am a production incident responder for a 5-machine factory. "
            "I learn from every incident resolution to diagnose faster and more accurately.",
    # adapted from self-driving-agents engineering/ops/bank-template.json
    retain_mission="Extract incident symptoms, the evidence that identified the root cause, "
                   "which remediation actions worked or failed and why, and time to recovery.",
    observations_mission="Observations are stable facts about recurring incident classes: their "
                         "signatures, the fix that works, fixes that only give temporary relief, "
                         "and which evidence identifies them fastest. Ignore one-off noise such as "
                         "exact percentages, timestamps and machine IDs.",
)
```

Then ensure the **Incident Patterns** mental model exists (6.5).

### 6.2 The Episode Record (heart of the project)

Written via `retain()` on every incident resolution. Rendered as **prose** (Hindsight extracts facts from text; the raw text itself is not stored as a memory, so it is also saved in the SQLite `episodes` table for the Memory Browser).

```
INCIDENT {incident_id} — {sim timestamp}
MACHINE: {machine_id} ({machine_profile})
SIGNATURE: {one generalized line, no IDs/numbers, e.g. "gradual throughput decline that
began shortly after a configuration deployment; controller timing and motion errors"}

SYMPTOMS: throughput {delta}% over {duration} min, onset {gradual|sudden},
error logs: {log_lines}

CONTEXT: config changed {minutes_before} min before onset; calibration age
{days} days; network status {status}; sensor variance {variance}.

DIAGNOSIS: {diagnosis}, confidence {conf}.

ACTIONS ATTEMPTED:
1. {action_1} → {effect_1} ({recovery_pct_1}%{, re-degraded after Xs})
2. {action_2} → {effect_2} ...

INVESTIGATION PATH: {tool_1}({args}) → {finding}; {tool_2}(...) → {finding} ...
DECISIVE EVIDENCE: {e.g. "recent events showed config v2.15.0 deployed 12 min before
onset"} ({n} tool calls; {n_useful} were decisive).

RESOLUTION: final action {final_action}, MTTR {mttr} sim-minutes.
OUTCOME: {successful|escalated}.
LESSON: {one-line lesson, e.g. "Restart gives only temporary relief for
config-regression signatures; go straight to rollback."}
```

`DIAGNOSIS` and `SIGNATURE` are the **agent's** words, never the simulator's ground-truth type. The `SIGNATURE` line is written by the `decide` LLM (a `signature` field on `Recommendation`), generalized so it carries no machine IDs, percentages or version strings — it is what lets the reranker line up a new incident with past ones (measured: Section 14). If the LLM is unavailable, `learn` falls back to a deterministic template built from the evidence flags.

Retain call:

```python
client.retain(
    bank_id=bank_id,
    content=episode_text,
    context="production incident resolution",
    timestamp=incident_sim_datetime,          # enables temporal recall on seeded history
    document_id=incident_id,                  # idempotent upsert
    metadata={"incident_id": ..., "machine_id": ..., "diagnosis": ...,
              "final_action": ..., "outcome": ..., "record_kind": "episode"},
    tags=["kind:episode"],
    retain_async=False,                       # synchronous: recallable immediately
)
```

**Partial lessons:** when `verify` detects a trap fix (partial / no effect), `record_lesson` retains immediately with `document_id=f"{incident_id}:lesson-{n}"`, `tags=["kind:lesson"]` — so the learning-from-failure beat persists even if the judge walks away mid-incident.

**Outage handling:** every episode/lesson is written to the SQLite `episodes` table first (`retain_status=pending`), then retained. On failure → `failed`, an `error` SSE event (`MEMORY_WRITE_FAILED`), and a background retry loop. The demo never crashes because Hindsight hiccuped.

### 6.3 Two Memory Touchpoints (Recall)

| # | Node | When | Query built from | What it changes |
|---|---|---|---|---|
| 1 | `recall_hints` | before investigation | the alert only (machine type, symptom shape) + the **Incident Patterns** mental model | tells `investigate` which evidence was decisive in similar past incidents (from `INVESTIGATION PATH` / `DECISIVE EVIDENCE`) → **fewer tool calls** (speed) |
| 2 | `search_memory` | after investigation | the evidence bundle | matched past episodes + known-bad actions → **correct first action, higher confidence** (accuracy) |

Both are deterministic code (no LLM decides whether to use memory). Both are skipped when memory is OFF.

**Query construction — observed signals only, paraphrased, never keyword-copied** (proves semantic matching, not string matching):

```
"{onset} output decline {with/after <each POSITIVE signal found in the evidence>}"
e.g. "gradual output decline beginning shortly after a new configuration was deployed;
      axis drive response timeouts"
```

- Only signals that are **present** go in the query. Clauses like "no config change" or "calibration recent" pull in exactly the incidents where config/calibration mattered (measured in Phase 0.5: they caused a false match to outrank a true one).
- Paraphrasing uses a small deterministic synonym table in `memory/render.py` (e.g. "servo timeout" → "axis drive response timeouts"), not an LLM, so it's reproducible. Machine IDs, version strings and exact numbers never go in the query.

**Recall call:**

```python
resp = client.recall(
    bank_id=bank_id, query=query,
    types=["world", "experience", "observation"],
    budget="mid", max_tokens=4096,
    include_source_facts=True,
)
```

**Match scoring** (each result has `scores.final`, `scores.reranker` (0–1), `scores.semantic` (cosine 0–1), `scores.keyword`). Measured in Phase 0.5: semantic cosine is ~0.66–0.78 for *every* factory incident, true or false, so it cannot gate matches; the cross-encoder reranker can.
- Group raw facts (`world`/`experience`) by `metadata.incident_id` → one **incident match** per past incident, with `rerank = max scores.reranker` and `similarity = max scores.semantic` over its facts, and `rank` = position of its first fact.
- **Match rule:** `rerank ≥ MEMORY_MATCH_REL_RERANK × top_rerank` (0.15) **and** `rerank ≥ MEMORY_MATCH_MIN_RERANK` (0.05, lets recall abstain when nothing is relevant). Measured: weakest true match ≥ 0.245 × top, strongest false match ≤ 0.056 × top.
- **UI shows** rank (#1, #2…) and the reranker-based strength; the `decide` LLM still makes the final call on fit (prompt rule 3), so a borderline match is visible but can be rejected with a reason.
- Both values are re-checked by the eval suite's retrieval metrics (11.3) on the full battery.
- `observation` results are shown separately as **"Learned patterns"** (consolidated beliefs like "restart only gives temporary relief for config regressions"). Consolidation runs in the background after retain, so observations may lag a few seconds; the demo does not depend on them being instant.

Output to state: `memory_results = [{incident_id, rank, rerank, similarity, summary, final_action, outcome}]`, `learned_patterns = [str]`.

### 6.4 Memory Toggle Semantics

The toggle's value is sent with each incident trigger (`memory_enabled`) and stored on the incident.

- **OFF:** `recall_hints` and `search_memory` return empty and emit `memory_skipped`. The LLM has no memory tool, so nothing can leak in. Everything else is identical.
- **Retain still happens when OFF** — the agent keeps accumulating experience; only its *use* of memory is disabled. The Learning tab splits series by `memory_enabled` so ON vs OFF is directly comparable.

### 6.5 Mental Model — "Incident Patterns" (core, Phase 3)

Hindsight's highest knowledge layer: a standing answer that Hindsight rewrites in the background as memories consolidate. Created once per bank at startup (idempotent: skip if a model with this `id` exists), config adapted from Vectorize's ops bank template:

```python
client.create_mental_model(
    bank_id=bank_id,
    id="incident-patterns",
    name="Incident Patterns",
    source_query="What incident classes recur on this factory floor? For each: its signature, "
                 "the evidence that identifies it fastest, the fix that works, and fixes that "
                 "only give temporary relief or no effect.",
    max_tokens=4096,
    trigger={"refresh_after_consolidation": True, "mode": "delta",
             "exclude_mental_models": True, "fact_types": ["observation"]},
)
```

Used in two places:
- **UI:** "Runbook the agent wrote itself" panel (Memory Browser + incident panel), refreshed on `memory_written`. Nobody writes this runbook — it appears and improves as incidents happen. Strong visual proof for the "Use of Hindsight" criterion.
- **Agent:** `recall_hints` reads it (a cheap DB read, no LLM) and injects it into the investigate prompt. Skipped when memory is OFF.

It refreshes asynchronously after consolidation, so it lags the latest incident by seconds to a minute. Nothing on the demo's critical path waits for it — `search_memory`'s raw-fact matches carry the immediate "cites INC-001" beat.

### 6.6 Seeded 6-Month History (makes the world feel lived-in)

Goal: a judge clicks **[LOAD 6-MONTH OPS HISTORY]** and the agent is instantly "experienced" — and the Learning tab shows a long, realistic curve.

**Generation (one-off, `scripts/generate_history.py` → `fixtures/seed_history.jsonl`, committed):**
- **Facts come from the simulator**, not the LLM: incident class, machine, signature parameters, attempted actions and their effects are sampled with a fixed seed from the same templates and effects matrix the live simulator uses. So every seeded episode is consistent with how the world actually behaves.
- **Narrative comes from the LLM**: operator notes, shift handover remarks, escalation notes, vendor tickets — rendered into the same episode format as live incidents (6.2), plus a `RESPONDED BY:` line.
- ~48 incidents over 6 months, weighted to shift patterns (more on night shift and after planned maintenance windows), weekends quieter.
- Fictional operator roster (e.g. "Priya Sharma, on-call", "Marcus Chen, maintenance lead", "Aisha Rahman, controls engineer") — clearly fictional, no real people.
- Includes: 2–3 **vendor escalations** (e.g. "escalated to Fanuc field service"), a few `ESCALATE_HUMAN` outcomes, and **2–3 deliberate contradictions** — e.g. an early episode where `RESTART_MACHINE` "resolved" resource exhaustion (the re-degradation happened after a shift change and was logged as a new incident), later episodes showing restart failing. These give Hindsight's observation consolidation something real to reconcile; the eval reports whether the Incident Patterns mental model ends up with the correct belief.

**Seeding (`make seed` → `scripts/seed_history.py`):** deletes and recreates `memoryops-seeded`, retains each fixture row with its historical `timestamp` (so temporal recall works: "6 weeks ago"), and writes matching rows into SQLite so the Memory Browser and Learning tab show the history. ~48 × ~3.5 s ≈ 3 min, so it runs **before** the demo; the UI button just switches the active bank (it never seeds live).

**Isolation:** the seeded bank is never used by the eval suite's cold-start conditions, so history cannot leak into learning-curve measurements.

---

## 7. Agent Design (Phase 2)

### 7.1 State

```python
class AgentState(TypedDict):
    incident: dict                 # id, machine_id, alert summary (no ground-truth type)
    memory_enabled: bool
    hints: list[str]               # from recall_hints
    evidence: list[dict]           # tool calls + results, prior attempts
    tool_call_count: int
    memory_results: list[dict]     # [{incident_id, score, summary, final_action, outcome}]
    learned_patterns: list[str]
    recommendation: dict           # Recommendation model (7.4)
    attempts: list[dict]           # [{action, effect, recovery_pct, re_degraded}]
    outcome: dict | None           # latest verify result
    attempt_count: int             # max 3 (= retry_count < 2 in v1.0 terms)
```

### 7.2 Graph

```
START
  → recall_hints          (skip if memory OFF)
  → investigate           (LLM tool loop, ≤10 calls)
  → search_memory         (skip if memory OFF)
  → decide                (LLM → validated Recommendation)
  → request_approval      (human wait starts here — excluded from MTTR)
  → act ⏸                 (interrupt(); human picks action; executes via adapter)
  → route_after_act:
       ESCALATE_HUMAN ─────────────────────────────→ learn → END
       otherwise → verify (wait VERIFY_WINDOW_SIM_S, then observe_recovery)
  → route_after_verify:
       full_recovery ──────────────────────────────→ learn → END
       partial/no_effect and attempt_count < 3 ────→ record_lesson → investigate
       partial/no_effect and attempt_count == 3 ───→ escalate → learn → END
```

Implemented in `backend/agent/graph.py` (async nodes in `backend/agent/nodes/`), driven by `AgentRunner` (`start` / `resume` / `pending_recommendation`). Any unexpected exception becomes an `error{AGENT_ERROR}` event plus an escalation — an incident is never left half-handled.

- `recall_hints`: recall on the alert; hints like "in similar past incidents, recent config deploys were the root cause" are injected into the investigate prompt.
- `investigate`: LLM tool-calling loop over the 4 tools. Max **10 tool calls** (guardrail 2). Every call and result streams as SSE. Ends with an `investigation_summary`. On retry it also sees the previous attempts ("RESTART_MACHINE → recovered to 70%, re-degraded after 90s").
- `search_memory`: deterministic recall (6.3).
- `decide`: LLM → `Recommendation` (Pydantic, whitelist). Must cite matched incident IDs when memory informed the decision.
- `request_approval`: **no LLM**. Emits `awaiting_action` and, in human mode, `lifecycle.acknowledge()` (starts the human-wait clock). A separate node because LangGraph re-runs a node from its start on resume — putting this in `act` would reset the wait clock.
- `act`: **no LLM**. `interrupt({"recommendation": ...})` pauses the graph; state lives in `InMemorySaver` keyed by `thread_id=incident_id`. `POST /api/incident/{id}/action` resumes with `Command(resume={"action": ...})`. The judge may pick *any* whitelisted action (this is the sabotage beat). In `auto_approve` mode (eval harness), the recommended action is applied without interrupting.
- `verify`: **no LLM**. Waits out the verify window, then reads `observe_recovery`: `held` → `full_recovery`; `recovered` but not held → `partial_recovery` (a trap fix wearing off); never recovered → `no_effect`. The agent infers the effect — it is never told. This is what catches trap fixes.
- `record_lesson`: renders + retains a partial lesson (6.2), then loops.
- `escalate`: attempts exhausted → `ESCALATE_HUMAN` through the adapter.
- `learn`: `lifecycle.resolve()` (MTTR from the ticket system), renders + retains the episode, emits `outcome` and `memory_written`, and returns the run's facts (`final`). Ground-truth scoring (was the first recommendation correct?) is done by the eval harness, never inside the agent.

### 7.3 Tools (exactly 4 — the LLM's interface to the world)

```python
get_machine_metrics(machine_id: str) -> dict
    # Current snapshot: throughput, oee, error_rate, temperature, sensor_variance,
    # packet_loss, latency, memory_pct, config_version, calibration age. Use first,
    # and on neighbouring machines to check for network-wide problems.
get_metric_history(machine_id: str, metric: str, window_hours: int) -> list[dict]
    # Time series for one metric. Use to tell gradual vs sudden onset and to spot
    # slow multi-day trends (e.g. memory climbing).
get_recent_events(machine_id: str, window_minutes: int) -> list[dict]
    # Config deploys, calibrations, restarts, OOM kills. Use to find what changed.
get_error_logs(machine_id: str, window_minutes: int) -> list[str]
    # Controller/PLC log lines. Use to see the error pattern.
```

Each tool is a thin wrapper over the matching `EnvironmentAdapter` method (5.9): arguments are validated with Pydantic before the call, results are serialized compactly for the LLM.

There is **no memory tool for the LLM** (v1.0 had `search_incident_history`). Memory access is deterministic in `recall_hints` / `search_memory`, which keeps the ON/OFF comparison clean and the memory calls visible in the trace.

### 7.4 Prompts & Output Schema

**investigate system prompt:** role = senior production incident responder; use tools to build evidence; do not guess before gathering data; stop as soon as the evidence is sufficient; finish with a structured `investigation_summary` (onset shape, key signals, what changed, what is ruled out). When hints are present: "Past incidents suggest checking X first."

**decide system prompt** — requirements:
1. Weigh evidence AND memory matches; state which matched incidents you rely on.
2. If memory matches exist, cite their incident IDs and what fix worked/failed there.
3. If a memory match doesn't fit this incident's signature, say so and ignore it.
4. Recommend ONE primary action + confidence + reasoning.
5. List `actions_known_to_fail` for this signature from memory (drives the trap-avoidance story).
6. If evidence is inconclusive, recommend `ESCALATE_HUMAN`.

```python
class Recommendation(BaseModel):
    action: Literal["ROLLBACK_CONFIG", "RESTART_MACHINE", "RECALIBRATE_SENSOR",
                    "RESTART_GATEWAY", "CLEAR_CACHE", "ESCALATE_HUMAN"]
    diagnosis: str                      # short label, e.g. "config regression"
    signature: str                      # generalized one-liner, no IDs/numbers (episode SIGNATURE)
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    cited_incidents: list[str] = []     # must be ⊆ memory_results incident_ids
    actions_known_to_fail: list[str] = []
```

**Confidence:** store both the LLM's self-reported `confidence` and a `calibrated_confidence` (initial formula, to be tuned after simulations: blend of LLM confidence, evidence coverage, and the best memory match score whose recorded successful action equals the recommendation). The UI shows the calibrated value; both are logged.

### 7.5 LLM Client (llm.py)

- **Routes, not a single provider:** OpenAI `gpt-5.4-mini` primary, Groq `openai/gpt-oss-120b` fallback — both through the OpenAI SDK (`langfuse.openai` traces every call), each with its **own request parameters** (OpenAI rejects Groq's `include_reasoning`; measured). A provider with no key is skipped (CI).
- Each route: retry ×2 with backoff; a **total time budget per call** (45 s) caps everything; total failure → `ESCALATE_HUMAN` recommendation (graceful degradation, never a crash).
- **429:** with a successor route → switch now (separate quota); on the last route → wait as long as the provider asks ("try again in 8.265s"), capped at 15 s.
- **400 naming an unsupported parameter** (e.g. `temperature` on some models): drop it for that route, retry at once, remember it. Measured live: this is what kept runs working when `reasoning_effort` was misconfigured (see 14.1c).
- **OpenAI constraints for `gpt-5.4-mini` on Chat Completions (measured 2026-09-28):** function tools require `reasoning_effort=none`, and `temperature=0` is only accepted with `none` — so the agent runs `reasoning_effort=none, temperature=0, seed=42`. (`gpt-5.5` rejects `temperature=0` entirely; `gpt-4.1-mini` works but is slower.)
- **Groq specifics (fallback):** `extra_body={"include_reasoning": False}`; `qwen/qwen3-32b` is retired (404), `qwen/qwen3.8-27b` also works; free tier 8,000 tokens/min per model.
- **Parallel tool calls:** a model may return several in one turn — execute all, count each toward the 10-call cap. Each tool result tells the model how many calls remain (measured: cut forced conclusions from 6/6 to 2/6 incidents).
- **Argument validation:** a model silently coerced an invalid machine ID (`Z9` → `M1`); every tool call's arguments are validated with Pydantic before execution.
- **Malformed output:** HTTP 400 `tool_use_failed` / `json_validate_failed` → salvage `failed_generation`: a real tool call is executed; a made-up tool (seen: `json`) carrying the answer becomes the text reply; prose is returned as text for a corrective retry.
- Any `<think>…</think>` block is stripped before parsing.
- Structured output for `decide`: JSON mode, validated with `Recommendation`; one corrective retry (guardrails 1/3).

### 7.6 Spend & Token Tracking (backend/usage.py)

- **Usage ledger** (`data/usage.db`, persistent, git-ignored): one row per LLM attempt — run label, incident, agent step, provider, model, prompt / cached / completion / reasoning tokens, cost, latency, error — plus one row per Hindsight retain with the tokens Hindsight reports.
- **Cost** from `PRICES` (USD per 1M tokens, cached input discounted), sourced from OpenAI's published Standard-tier price list (fetched 2026-09-28): `gpt-5.4-mini` $0.75 in / $0.075 cached / $4.50 out. Groq calls cost $0 on the free plan (tokens still counted). An unpriced model is reported as such, never guessed.
- **Hard cap:** `LLM_SPEND_CAP_USD` (default $10) is checked against the ledger's all-time total before every call; reaching it raises `LLMBudgetExceeded` → `error{LLM_BUDGET_EXCEEDED}` → escalation.
- **Reports:** `make usage` (all-time, by model, by agent step, recent runs, $/incident); `scripts/demo_dryrun.py` prints tokens and cost per incident; the eval report will include the same.

---

## 8. Guardrails (guardrails.py)

1. **Pydantic validation** on all LLM outputs (`Recommendation`; `cited_incidents` must be real matched IDs).
2. **Max 10 tool calls** per investigation → forced conclusion.
3. **Action whitelist** (6 actions, 5.4) — invalid actions rejected + corrective retry; never executed.
4. **Dependency retry + fallback** — LLM retry/model fallback (7.5); Hindsight failures degrade to "memory unavailable" for recall and outbox-retry for retain (6.2).
5. **Simulator state validation** — can't trigger on an already-degraded machine; **one active incident at a time** (keeps the demo legible); can't act on a closed incident. Violations return a structured `error` event, not a 500.
6. **Memory match rule** — reranker-relative gate (`MEMORY_MATCH_REL_RERANK` 0.15 × top, floor `MEMORY_MATCH_MIN_RERANK` 0.05; measured in Phase 0.5); below it, results are not shown as "matches".

---

## 9. FastAPI + SSE (Phase 3)

**Endpoints:**

```
GET  /api/health                      # config + reachability of Groq / Hindsight / Langfuse
GET  /api/state                       # machines + KPIs (initial load)
GET  /api/stream                      # SSE — ONE global stream; supports Last-Event-ID replay
POST /api/incident/predefined         # {type, machine?, memory_enabled, auto_approve?}
POST /api/incident/custom             # structured builder payload + memory_enabled
POST /api/incident/random             # {memory_enabled}
POST /api/incident/{id}/action        # {action} → resumes graph
POST /api/incident/{id}/ignore        # cascade; stays awaiting_action
GET  /api/incidents                   # history for Memory Browser
GET  /api/incidents/{id}              # episode text(s) + stored trace events
GET  /api/metrics                     # learning-curve series
GET  /api/memory/runbook              # current Incident Patterns mental model content
GET  /api/eval/latest                 # latest committed eval results.json (Learning tab)
POST /api/admin/reset                 # {bank: "live"|"seeded", wipe_memory: bool}
```

**Why one global stream:** the frontend opens it once at page load, so there is no race between "POST created the incident" and "client subscribed". Every event carries `incident_id` (or null). The event bus keeps a ring buffer (last ~2000 events) with monotonically increasing IDs; `EventSource` reconnects replay from `Last-Event-ID`.

**SSE event types (contract with frontend):**

```
state_changed        {machines: [...], gateways: [...], line_throughput, oee, alerts}  # ~1 Hz
incident_triggered   {incident}
investigation_start  {incident_id, attempt}
memory_hints         {query, hints: [...]}                        # recall_hints
memory_skipped       {node}                                       # memory OFF
tool_call            {tool_name, args}
tool_result          {tool_name, result}
investigation_summary{summary, tool_call_count}
memory_search        {query}
memory_results       {matches: [{incident_id, score, summary}], learned_patterns: [...]}
recommendation       {action, diagnosis, confidence, calibrated_confidence,
                      reasoning, cited_incidents, actions_known_to_fail}
awaiting_action      {options: [...]}
action_executed      {action, effect, recovery_pct}
verifying            {window_sim_s}
re_degradation       {incident_id, message}                       # trap fix wearing off
lesson_written       {document_id, text}
outcome              {resolved: bool, escalated: bool, mttr_sim_s}
memory_written       {document_id, episode_summary}
runbook_updated      {content, updated_at}                        # mental model changed
metrics_updated      {series}
error                {code, message}                              # never crash
```

---

## 10. Frontend (Phase 4) — Industrial Control Room UI

### 10.1 Philosophy

A dark industrial control room (SCADA / Grafana style): a **spatial plant floor with live signals and failures you can see**. Not a chatbot, not a game.

**Purple is reserved exclusively for memory.** Every element that comes from Hindsight (memory hints, matches, known-to-fail chips, lessons, the runbook, memory toasts, the MEMORY toggle itself) is purple, and nothing else is. Judges learn the colour in the first minute, and from then on the memory story is visible at a glance. When memory is OFF, purple elements turn grey, so the before/after is visual too.

### 10.2 Layout (single screen, judge never navigates away during the demo)

```
┌──────────────────────────────────────────────────────────────────────┐
│ MEMORYOPS · SECTOR 7 │ OEE · LINE THROUGHPUT · ALERTS · SIM CLOCK │  │
│                      │ BANK: live|seeded        🧠 MEMORY [ON/OFF] │  │
├─────────────────────────────────────────┬────────────────────────────┤
│                                         │ 🔍 INVESTIGATION TRACE     │
│   PLANT FLOOR  (world view, ~65%)       │    streams live            │
│   FEED → M1 → M2 → M3 → M4 → SHIP       ├────────────────────────────┤
│              ▲    M5    ▲               │ 🧠 MEMORY  (hints, matches,│
│   GW-A · · · · · · GW-B  (net links)    │    learned patterns)       │
│                                         ├────────────────────────────┤
│                                         │ ▶ RECOMMENDATION + ACTIONS │
├─────────────────────────────────────────┴────────────────────────────┤
│ [CONFIG REGRESSION] [SENSOR DRIFT] [NETWORK] [RESOURCE] [CUSTOM]     │
│ [SURPRISE ME] [LOAD 6-MONTH OPS HISTORY]  ·  FLOOR · MEMORY · LEARNING│
└──────────────────────────────────────────────────────────────────────┘
```

- Designed for 1920×1080 and 1440×900 (laptop + projector). Trace text ≥ 14px; high contrast for projectors.
- Reset lives in a small overflow menu with a confirm dialog, never next to the trigger buttons.
- Trigger buttons disable while an incident is active (mirrors guardrail 5), with a tooltip saying why.

### 10.3 Plant Floor (hand-rolled SVG)

- **Nodes:** each machine shows name + short profile, throughput bar, alert count, 60-point sparkline, and a small gauge (memory % for M-nodes). Gateways are separate small nodes; network links are faint dashed lines to each machine they serve.
- **States** (never colour alone — each also has an icon, a label and, for critical, a pulse): healthy `#3FB950` ● · degraded `#D29922` ▲ · critical `#F85149` ✖ pulsing · recovering / verifying `#58A6FF` ↻.
- **Flow:** animated `stroke-dashoffset` dots on line links; flow slows and dims downstream of a degraded stage (line-level starvation, 5.2).
- **Trace-linked focus:** when a `tool_call` targets a machine, that node gets a blue focus ring, so the audience sees *where* the agent is looking. (Blue, not purple: this is the agent, not memory.)

**Visual signature per incident class (must be distinguishable without reading text):**

| Class | Visual |
|---|---|
| `config_regression` | gradual dim of one node over ~30–60 s real time; small "cfg" event tick appears on its sparkline |
| `sensor_drift` | jittery sparkline + phantom temperature alarm badges that flicker |
| `network_failure` | **gateway links turn red first**, then all machines on that gateway drop at once, then downstream flow dims |
| `resource_exhaustion` | the node's memory gauge fills toward red, then OOM badge |

**Re-degradation (money shot):** the "recovered" machine flashes red ×3, drops, and a toast appears: *"RESTART_MACHINE gave only temporary relief — re-degraded after 90 s."* followed by a purple toast when the lesson is written to memory.

### 10.4 Right-Rail Panels

1. **Investigation trace** — terminal style, one line per step, streamed over SSE: `▸ get_recent_events(M3, 30m)` → collapsible result. Memory lines (`🧠 recall_hints`, `🧠 search_memory`, `🧠 retain`) are purple. Guardrail/retry lines are amber. Auto-scrolls; pauses scrolling when the user scrolls up.
2. **Memory panel** (purple) —
   - *Hints* used to steer the investigation.
   - *Matched incidents* as chips: `INC-001 · #1 · strong` — rank plus strength from the match rule (6.3). Tooltip shows raw reranker/semantic scores, diagnosis, final action, outcome, date, operator (seeded). Clicking opens it in the Memory Browser. **No raw similarity numbers as badges** — we measured them as non-discriminative (14.1).
   - *Learned patterns* (observations).
   - Memory OFF → grey panel: "Memory disabled — agent reasoning from evidence only."
3. **Recommendation card** — slides in on `recommendation`. States:
   - `recommending`: action, diagnosis, confidence bar, reasoning, cited incidents (purple chips), **⚠ known to fail** chips (purple border, from memory).
   - `awaiting action`: buttons — recommended action **filled**, other whitelisted actions **outline**, IGNORE and ESCALATE separated.
   - `verifying`: progress bar over the verify window (`verifying recovery… 12 s`), machine node shows the blue recovering state.
   - `outcome`: resolved ✓ with MTTR (sim) and tool calls, or re-degraded ✖ → card returns to `recommending` for the next attempt.

### 10.5 Tabs

- **FLOOR** — the layout above.
- **MEMORY BROWSER** — every episode and lesson: list (ID, class, machine, outcome, date, operator) → expand to the exact text retained to Hindsight + metadata + "pending sync" badge if in the outbox. Side panel: **"Runbook the agent wrote itself"** — the Incident Patterns mental model (6.5), purple, with last-updated time.
- **LEARNING** (Recharts) — titled **"The agent is learning"**:
  - Primary: the latest committed eval report (`GET /api/eval/latest`): recommendation accuracy, MTTR, tool calls by exposure (1st, 2nd, 3rd+), **memory ON (purple) vs OFF (grey)** with 95% CI bands.
  - Secondary: this session's live incidents as points over the same axes.
  - No hardcoded numbers anywhere; axis labels and captions come from the data.

### 10.6 Custom Builder Modal (structured, no free text)

Machine ▾ (M1–M5) · config changed [Y/N] + minutes-before slider · throughput Δ slider (−50…−5) · error-rate slider · temperature [Normal/High] · calibration [Fresh/30+ days] · network [Normal/Degraded] · memory trend [Flat/Climbing] · **[RUN]**. A small "signal preview" shows which signals will be present — never the class the simulator will assign.

### 10.7 Design Tokens

| Token | Value | Use |
|---|---|---|
| `bg` | `#0B0F14` | page |
| `panel` | `#11161D` | panels, nodes |
| `border` | `#232B36` | dividers |
| `text` / `text-muted` | `#E6EDF3` / `#8B949E` | copy |
| `healthy` | `#3FB950` | machine OK |
| `degraded` | `#D29922` | warnings, retries |
| `critical` | `#F85149` | failures |
| `recovering` / `focus` | `#58A6FF` | verifying, agent focus ring |
| **`memory`** | **`#A371F7`** | **anything from Hindsight — nothing else** |
| `memory-off` | `#6E7681` | memory elements when memory is OFF |

Monospace for trace, IDs and numbers; system sans for everything else (no font CDN).

### 10.8 SSE → UI Mapping

| Event | UI reaction |
|---|---|
| `state_changed` | update nodes, links, KPIs (≈1 Hz) |
| `incident_triggered` | focus the node, pulse its zone, open the right rail |
| `investigation_start` | trace header `Attempt n` |
| `memory_hints` / `memory_skipped` | purple hint lines / grey "memory disabled" line |
| `tool_call` / `tool_result` | trace lines; blue focus ring on the targeted node |
| `memory_search` / `memory_results` | purple trace line; memory panel fills with chips |
| `recommendation` | card slides in (`recommending`) |
| `awaiting_action` | action buttons enable |
| `action_executed` / `verifying` | card → `verifying`; node → recovering |
| `re_degradation` | red flash ×3 + toast |
| `lesson_written` / `memory_written` | purple toast; Memory Browser gains a row |
| `runbook_updated` | runbook panel refreshes with a purple highlight |
| `outcome` | card → `outcome` |
| `metrics_updated` | Learning tab adds the live point |
| `error` | calm designed error card (17.4) — **never a crash, never a stack trace** |

### 10.9 Tech, Timebox & Fallback

- Next.js (App Router) + Tailwind + shadcn/ui + Recharts + **hand-rolled SVG**. No game engine, no canvas library, no Three.js.
- Animations are CSS (`stroke-dashoffset`, keyframes); React re-renders only on SSE state (~1 Hz), never per animation frame.
- **Timebox:** 1 day for the plant floor, 1 day for the panels + tabs.
- **Fallback if the floor overruns:** a clean machine-card grid in line order with arrow connectors and the same states/colours — every panel, event mapping and demo beat still works.
- **Non-negotiable:** the memory panel, the recommendation card states, and the Learning charts.

## 11. Evaluation Suite (the differentiator)

Most teams will *claim* their agent improves. We **measure** it, with a headless, reproducible suite whose report is committed to the repo. One artifact that serves four purposes: proof for judges, data for the Learning tab, content for the article, and the agent's regression test before every demo (`make eval`).

### 11.1 Per-incident metrics (`backend/eval/metrics.py`, also recorded for live incidents)

```sql
metrics(run_id TEXT, condition TEXT, seed INTEGER, position INTEGER,
        incident_id TEXT, true_type TEXT, diagnosis TEXT, machine_id TEXT,
        exposure INTEGER,                   -- k-th time this class appears in this run
        memory_enabled INTEGER,
        mttr_sim_s INTEGER, agent_time_real_s REAL, human_wait_sim_s INTEGER,
        tool_calls INTEGER, attempts INTEGER,
        investigation_efficiency REAL,      -- decisive tool calls / total tool calls
        llm_confidence REAL, calibrated_confidence REAL,
        memory_hit INTEGER,                 -- any match passed the gate (6.3)
        top_match_same_class INTEGER,       -- retrieval quality: rank-1 match is same class
        recommendation_correct INTEGER,     -- agent's FIRST recommendation == correct fix
        first_time_right INTEGER,           -- first EXECUTED action == full recovery
        false_replay INTEGER,               -- recommended a fix that belongs to another class
        escalated INTEGER,
        llm_tokens INTEGER, hindsight_tokens INTEGER,
        ts INTEGER)
```

`recommendation_correct` measures the agent; `first_time_right` depends on what a human clicked — both are tracked, charts default to the agent-side metric.

### 11.2 Battery & experimental design

- **Scenario variants:** 4 classes × 3 variants = 12 (variants differ in machine, magnitude, onset timing and log wording), plus noise per occurrence from the templates.
- **Run sequence:** 24 incidents per run (6 per class), interleaved so classes alternate. Each class's first two exposures happen on M1–M3; later exposures deliberately land on **held-out machines M4–M5** (transfer test). A sensor-drift incident is always placed right after config-regression learning (discrimination test).
- **Conditions:** `memory_on` vs `memory_off`, **paired** — identical incident sequence and seeds; each (condition, seed) gets a fresh throwaway bank.
- **Seeds:** 3 per condition (`--seeds`), controlling incident sampling and noise. LLM calls use `temperature=0` and a fixed `seed` where the API supports it; residual LLM nondeterminism is what the seeds + CIs absorb.
- **Mode:** `auto_approve` (the recommended action is executed), **fast-forward clock** (sim time advances instantly; no real sleeping on verify windows), so a run costs only LLM + Hindsight latency.
- Default full run: 24 × 2 × 3 = **144 incident runs**. `--quick`: 1 seed, 12 incidents, for smoke checks.

### 11.3 What the report measures

| Question | Metric | How it's reported |
|---|---|---|
| Does it learn? | recommendation accuracy, MTTR, tool calls, confidence **by exposure** (1st, 2nd, 3rd+) | mean + bootstrap 95% CI, ON vs OFF side by side |
| Is the gain caused by memory? | paired difference ON − OFF (same seed, same position) | mean difference + 95% CI; CI excluding 0 = real effect |
| Does it transfer across machines? | accuracy on first held-out-machine exposure after ≥1 exposure elsewhere | rate + CI, and rank of the prior same-class incident |
| Does it discriminate? | `false_replay` count on the discrimination probes | "k / n false replays" |
| Is retrieval good? | `top_match_same_class` (recall@1), gate precision | rate + CI; re-checks the 6.3 match rule at scale |
| Is confidence honest? | Brier score of `calibrated_confidence` vs `recommendation_correct` | ON vs OFF; reliability table |
| What does it cost? | LLM tokens, Hindsight tokens, real seconds per incident | mean per incident, total per run |
| Where does it fail? | all incorrect recommendations, grouped by class | listed with incident IDs and Langfuse trace links |

**MTTR honesty:** MTTR runs on the simulated clock (5.1), whose costs are defined, so the report always shows MTTR's drivers next to it (tool calls, attempts, trap fixes applied).

### 11.4 Report & reproducibility

`make eval` → `scripts/run_eval.py` → `docs/eval/<date>-<git_sha>/`:
- `REPORT.md` — headline table, per-class tables by exposure, transfer, discrimination, retrieval, calibration, cost, failure list, and PASS/FAIL against 11.5.
- `charts/*.png` — learning curves by exposure (ON vs OFF with CI bands), paired-difference plot, calibration plot.
- `results.json` — every per-incident row; the Learning tab can load it (`GET /api/eval/latest`).
- **Run metadata** in the report header: git SHA, model IDs, all config values, seeds, timestamps, package versions.
- **Rate limits:** calls are throttled and the harness checkpoints after every incident, so an interrupted run resumes (`--resume`) instead of restarting. Throughput and Groq limits are measured on the first `--quick` run and the full run is sized to fit.
- CI never runs the live eval (costs money, needs keys); CI runs the harness end-to-end with a fake LLM + fake memory to prove the pipeline works.

### 11.5 Acceptance targets (report prints PASS/FAIL; misses are reported, not hidden)

1. Memory ON, exposure 3+: recommendation accuracy higher than OFF, paired 95% CI excluding 0.
2. Memory ON, exposure 3+: tool calls and MTTR lower than OFF, paired 95% CI excluding 0.
3. Memory OFF: no significant trend across exposures (sanity check that the gain is memory, not drift).
4. Discrimination: false replays ≤ 1 per 15 probes.
5. Retrieval: recall@1 ≥ 0.8 when a same-class prior exists.

If a target is missed, the fix is in the agent or memory design — never in the metric.

---

## 12. Demo Script (3.5 min — build everything to serve this)

Numbers in brackets are placeholders, filled from Phase 5 measurements.

1. **0:00** Healthy factory, `memoryops-live` bank (empty). "MemoryOps runs production, and it gets better at its job every day."
2. **0:15** Trigger config regression on M3 → cold investigation trace streams (many tool calls) → [~0.6] confidence → judge applies ROLLBACK → verify → recovery → **memory written — the episode appears in Memory Browser.**
3. **1:15** Trigger config regression again — **on M4, different log phrasing** → hints steer investigation (fewer tool calls) → INC-001 ranked #1 match, cited → resolved in [~3 sim-min]. "It matched the signature, not the machine."
4. **1:45** Sabotage: trigger config regression, apply **RESTART** instead → partial recovery → **re-degrades on stage** → lesson written → agent re-investigates, recommends rollback, lists RESTART under "known to fail".
5. **2:15** Sensor drift → agent does NOT replay "rollback" (different signature) → recommends recalibration. Discrimination proven.
6. **2:40** Learning tab: MTTR curve [cold → warm], tool calls falling, confidence rising.
7. **2:55** Toggle MEMORY OFF, trigger config regression → cold behavior again. Toggle ON → instant match. Live before/after.
8. **3:15** (Optional) **[LOAD 6-MONTH OPS HISTORY]** → agent cites an incident from [6 weeks] ago, responded to by a named operator. Close: "Day one it's a rookie. Every incident makes it better. That's what memory is for."

**Kept ready for Q&A (not in the 3.5 min):** the Langfuse trace of the incident just shown (every LLM call, tool result, token count); `docs/eval/REPORT.md`; the Hindsight Cloud UI showing the bank's facts and observations.

**Demo polish checklist:**
- Zero console errors, zero dead buttons, zero placeholder text — click every control before demo day.
- Streaming/loading state on everything: trace steps stream in, metrics animate, nothing "just appears".
- Designed error states (Section 17.4) rehearsed: revoke the Groq key mid-rehearsal → calm "LLM unavailable — escalated to human" card, no crash. (COULD: show this live on stage.)
- Rehearse 5+ times with a stopwatch, out loud. Reset → full script → reset.
- Fallback: the full stack running locally on a second laptop.

---

## 13. Build Order & Acceptance Criteria

**Phase 0 — Scaffold.** Repo layout (Section 4), `pyproject.toml` + lockfile, `.env.example` (Section 15), FastAPI skeleton with `/api/health`, Next.js skeleton. ✅ `uv sync` (or `pip install -e .`) + `uvicorn backend.main:app` boots; `/api/health` reports each dependency.

**Phase 0.5 — Spikes (first 1–2 hours of Phase 2 work, do before building nodes).**
- `spike_hindsight.py`: create bank, retain 3 synthetic episodes (2 config regression with different wording, 1 sensor drift), recall with a paraphrased query. ✅ Record: retain latency; recall-immediately-after-retain works; score distributions (`semantic`, `reranker`, `final`) for true vs false matches; observation lag. **Done — results in Section 14.**
- `spike_recall_design.py sig|nosig`: 5 episodes (all 4 classes), with vs without a `SIGNATURE:` line, observed-signals-only queries. ✅ Chooses the record format and match rule. **Done — results in Section 14.**
- `spike_groq.py`: list available models; tool-calling round trip on primary + fallback; provoke a malformed tool call and confirm the `tool_use_failed` / `failed_generation` shape; confirm `include_reasoning: false` suppresses reasoning. ✅ Record: fallback model choice, error shapes, typical latency. **Done — results in Section 14.**

**Phase 1 — Simulator + adapter + CI. ✔ DONE** (103 tests; mutation-checked: breaking the trap fix or its re-degradation fails the suite). CI workflow and `Makefile` land in the first Phase 1 commit, so every later commit is checked. ✅ `backend/adapters/base.py` Protocol + simulator implementation + datadog stub; `tests/test_adapter.py` passes; `pytest tests/test_simulator.py`: each of 4 types triggers on random machines; metrics degrade per taxonomy; every cell of the effects matrix (5.5) produces the specified effect incl. re-degradation timing; IGNORE cascades; custom builder maps to the right class incl. `ambiguous`; sim clock math; SQLite persists everything.

**Phase 2 — Agent. ✔ DONE** (158 tests; three live dry runs, 14.1b). ✅ `demo_dryrun.py` headless: config regression → real tool calls → valid recommendation → action executes → verify → episode retained (visible in Hindsight Cloud UI) → reworded same-type incident on another machine → recall returns prior episode above threshold → fewer tool calls, higher confidence, reasoning cites INC-ID → trap-fix loop works (restart → re-degrade → lesson retained → re-investigate → rollback) → memory OFF run shows no memory events.

**Phase 3 — API + SSE + Mental Model.** ✅ Incident Patterns mental model created at startup and readable after a few incidents; all endpoints work; one global SSE stream carries the full event contract; reconnect replays via Last-Event-ID; human-in-the-loop resume via action endpoint; guardrail violations and dependency failures emit `error` events instead of crashing.

**Phase 4 — Frontend.** ✅ Full demo script (Section 12) runs end-to-end in the browser, including memory toggle, re-degradation animation, and reset.

**Phase 5 — Evaluation suite.** ✅ `make eval --quick` runs end-to-end on live services; full run completes (with `--resume` if interrupted); `docs/eval/<run>/REPORT.md` committed with PASS/FAIL against 11.5; harness covered in CI with fakes; Learning tab reads `results.json`.

**Phase 6 — Seeded history.** ✅ `fixtures/seed_history.jsonl` generated and committed (~48 episodes, contradictions, escalations); `make seed` loads it; **[LOAD 6-MONTH OPS HISTORY]** switches banks; Memory Browser + Learning tab show the history.

**Phase 7 — Polish & rehearsal.** ✅ Real numbers from the committed eval report replace every placeholder (Sections 1, 12, 18, README); README with architecture diagram, eval table, CI badge, `/docs` link and "How Hindsight memory is used"; 6 ADRs; `make demo` / `docker compose up` verified from a fresh clone; demo polish checklist (Section 12) done; 5+ timed rehearsals. COULD: 500-episode load test (reported as its own measured result), offline fallback laptop, live failure demo.

**Incremental order inside phases:** config regression end-to-end first (sim → agent → API → UI), then sensor drift, then network failure and resource exhaustion, then custom builder, then IGNORE cascade. Tests are written with each piece, not after.

---

## 14. Measured Values

### 14.1 Phase 0.5 spike results (2026-09-28, Hindsight Cloud + Groq)

| Question | Result |
|---|---|
| Sync `retain` latency | 2.7–4.3 s per episode (~3.3k Hindsight tokens each) |
| Recallable immediately after retain? | Yes |
| `recall` latency | 0.13–0.16 s |
| Observation lag | First observation visible ~1 s after the third retain |
| Semantic cosine, true vs false matches | Overlapping: true 0.711–0.759, false 0.688–0.725 → **unusable as a gate** |
| Reranker with `SIGNATURE` line + observed-signals query | True incident ranked #1 for 5/5 queries; weakest true ≥ 0.245 × top; strongest false ≤ 0.056 × top |
| Reranker without `SIGNATURE` line | 4/5 correct at #1; one query had a false match at 0.927 × top → signature line adopted |
| Groq fallback `qwen/qwen3-32b` | **Gone** (404). Using `qwen/qwen3.8-27b` |
| Groq latency (tool call) | gpt-oss-120b 0.5 s (2.1 s for a long answer); qwen3.8-27b 0.3–0.4 s |
| Malformed tool call | HTTP 400 `tool_use_failed`; `failed_generation` = refusal prose, not JSON |
| `include_reasoning: false` | No reasoning field and no `<think>` in content on either model |

### 14.1b Phase 2 live dry runs (2026-09-28, `scripts/demo_dryrun.py`, 5-incident storyline, fast-forward clock)

| Run | Change since previous | First recs correct | False replays | LLM outages | Tokens / incident | Real s / incident |
|---|---|---|---|---|---|---|
| 1 | — | 5/5 | 0 | 0 | not recorded | 68–234 (rate-limit waits) |
| 2 | smaller tool output, 429 → switch model | 3/5 | 1 (sensor drift → ROLLBACK) | 2 | 8.6k | 8–25 |
| 3 | honour Groq retry-after, escalate on incomplete investigation, skip memory search on empty query | **5/5** | **0** | **0** | 19.6k | ~40–70 |

What the runs taught (all fixed and covered by regression tests):
- **Groq free tier is 8,000 tokens/minute per model.** An incident costs ~9–20k tokens, so the free tier sustains roughly one incident per minute across both models. Fine for the live demo; it bounds the eval suite's size (14.2).
- **Rate limits caused the only false replay** (run 2): both models throttled → investigation cut short → bare "output decline" memory query → a config-regression match → the decision replayed ROLLBACK on a sensor drift. `verify` caught it (no effect), a lesson was written and the retry recalibrated — self-correction worked, but the root cause was fixed: memory never decides without evidence.
- **The model sometimes "calls" a made-up tool named `json` to deliver its answer** (`tool_use_failed`); salvaged as the text reply.
- **Query paraphrasing needed whole-word matching** ("mes" inside "times") and idempotence.
- **Observed memory effect on these textbook classes is small on accuracy**: the cold agent already picks the right fix for config regression and sensor drift. Memory showed up as citations, cross-machine transfer, `actions_known_to_fail` after the sabotage lesson, and discrimination (retrieved config incidents for a sensor drift and correctly declined them). Where memory should move accuracy is on non-obvious fixes and trap avoidance; the eval suite measures this per class (11.3) rather than assuming it.
- **Retrieval precision is modest**: for a sensor drift, config-regression incidents scored rerank 0.30 (above the gate). The `decide` step, not the gate, does the discriminating; the eval reports gate precision separately.
- **Onset is usually reported "abrupt"**: at detection only ~2 min of a 5–10 min ramp has happened. Harmless for matching so far; tracked.
- Langfuse: one trace per run — e.g. the sabotage incident has 1 agent root, 17 generations, 6 tool calls, 4 chains, 3 retrievals, 2 memory writes.

### 14.1c OpenAI dry runs (2026-09-28, 6-incident storyline incl. a repeat sensor drift)

| Run | Change | First recs correct | Retries / fallbacks | Tokens / incident | Cost / incident |
|---|---|---|---|---|---|
| 4 | switched to OpenAI `gpt-5.4-mini` | 4/5 (sensor drift escalated) | 0 / 0 | 11.8k | $0.0113 |
| 5 | calibration schedule in metrics, absence filter | 4/5 (sensor drift escalated again) | 0 / 0 | 14.0k | $0.0125 |
| 6 | engineer fix recorded on escalations; +repeat sensor drift | **6/6** | 0 / 0 | 13.5k | $0.0124 |
| 7 | `reasoning_effort=none`, remaining-budget hint | **6/6** | 0 / 0 (0 failed calls) | 15.0k (32k cached) | **$0.0109** |

Findings:
- **Speed and reliability:** ~12–20 s per incident, zero rate-limit retries, versus 25–234 s on the Groq free tier.
- **The cold sensor drift is genuinely ambiguous for this model:** the reported temperature really climbs, so it escalated a possible real overheat in 2 of 4 cold runs (and recalibrated in 2) — even at `temperature=0`. This is exactly where memory helps: the repeat sensor drift on another machine cited the first and recalibrated with confidence 0.91–0.96. The eval measures it with seeds and CIs rather than trusting one run.
- **`reasoning_effort` was silently dropped** on the first call of every run (tools + reasoning unsupported on Chat Completions) — caught by the ledger's failed-call count, fixed by configuring `none` explicitly.
- **`investigate:force` was ~32% of spend** while the model never stopped on its own; the remaining-budget hint cut forced conclusions to 2 of 6 incidents.
- **Total spend for all development runs so far: $0.26** (ledger).

### 14.2 Still to be measured

| Item | Initial value | Set by |
|---|---|---|
| `MEMORY_MATCH_REL_RERANK` / `MEMORY_MATCH_MIN_RERANK` | 0.15 / 0.05 (from 14.1) | Re-checked by the eval suite's retrieval metrics (11.3) |
| Eval throughput and cost | Resolved by the OpenAI switch: ~15 s and ~$0.011 per incident (14.1c) → full battery (144 incident runs) ≈ $1.6 and well under an hour, estimated from measured averages | Confirmed by the first `make eval --quick` (Phase 5) |
| MTTR cost model for fast-forward mode | none yet: no sim time passes during LLM calls, so eval MTTR ≈ detection + verify window | Phase 5: charge fixed sim-seconds per tool call / LLM call and document it (5.1) |
| `SIM_SPEED` | 10 | Phase 4 demo rehearsal |
| `VERIFY_WINDOW_SIM_S` | 180 | Phase 1 (must exceed max re-degrade delay) |
| `ESCALATION_PENALTY_SIM_S` | 1800 | Phase 1 |
| Calibrated-confidence formula weights | TBD | Phase 5 eval (chosen to minimize Brier score on a held-out seed) |
| Headline MTTR / accuracy / confidence numbers | placeholders | Phase 5 eval report |

---

## 15. Configuration (.env.example)

```bash
# Hindsight
HINDSIGHT_BASE_URL=https://api.hindsight.vectorize.io
HINDSIGHT_API_KEY=
HINDSIGHT_BANK_LIVE=memoryops-live
HINDSIGHT_BANK_SEEDED=memoryops-seeded
# LLM: OpenAI primary, Groq fallback
OPENAI_API_KEY=
OPENAI_REASONING_EFFORT=none
GROQ_API_KEY=
GROQ_BASE_URL=https://api.groq.com/openai/v1
LLM_PRIMARY_PROVIDER=openai
LLM_PRIMARY_MODEL=gpt-5.4-mini
LLM_FALLBACK_PROVIDER=groq
LLM_FALLBACK_MODEL=openai/gpt-oss-120b
# Spend tracking
USAGE_DB_PATH=./data/usage.db
LLM_SPEND_CAP_USD=10
# Langfuse
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://us.cloud.langfuse.com
# Simulator / agent
SIM_SPEED=10
VERIFY_WINDOW_SIM_S=180
ESCALATION_PENALTY_SIM_S=1800
MEMORY_MATCH_REL_RERANK=0.15
MEMORY_MATCH_MIN_RERANK=0.05
MAX_TOOL_CALLS=10
MAX_ATTEMPTS=3
LLM_CALL_BUDGET_S=30
SQLITE_PATH=./data/memoryops.db
# Frontend
NEXT_PUBLIC_API_BASE=http://localhost:8000
```

---

## 16. Submission Checklist (from the problem statement)

- [ ] GitHub repository with clean, documented code
- [ ] README incl. **"How Hindsight memory is used"** section (retain/recall touchpoints, episode format, before/after evidence)
- [ ] Demo video (follows Section 12)
- [ ] Live demo rehearsed (reset → full script → reset) at least twice
- [ ] Content deliverables per team member: article, social media post, video (per the official content guide)
- [ ] Realistic data: machine profiles, log lines, config versions, seeded history all look real
- [ ] Committed eval report (`docs/eval/`) + its headline table in the README
- [ ] CI badge green on the submitted commit

---

## 17. Engineering Quality

### 17.1 Tests (pytest, no network)
Written alongside each phase; every external dependency is faked (LLM client, Hindsight client, clock). Target ≥ 40 tests by Phase 4. Coverage priorities: effects matrix (every cell), re-degradation timing, custom classifier, adapter contract, guardrails (whitelist, schema, tool-call cap, argument validation), LLM retry/fallback/`tool_use_failed`, graph routing (full, partial, exhausted, escalate paths), memory rendering + match rule, eval stats on known inputs, health checks.

### 17.2 CI (`.github/workflows/ci.yml`, runs on every push, **no secrets**)
`uv sync` → `ruff check` → `ruff format --check` → `mypy backend` → `pytest` → frontend `npm ci && npm run lint && npm run build`. README shows the badge. The live eval is never run in CI.

### 17.3 Architecture Decision Records (`docs/adr/`, ~10 lines each)
Context → Decision → Consequences. These double as the required "how Hindsight memory is used" explanation, in the format real engineering orgs use.
1. `0001-single-agent.md` — one protagonist, one memory, one measurable learning curve.
2. `0002-hindsight-only-memory.md` — no vector DB, no second memory; one bank per environment (live / seeded / eval).
3. `0003-environment-adapter.md` — agent sees six typed methods; simulator is one implementation (5.9).
4. `0004-deterministic-memory-touchpoints.md` — memory at two fixed points, not an LLM tool; clean ON/OFF comparison.
5. `0005-reranker-match-rule.md` — measured: semantic cosine doesn't separate matches, the reranker does (14.1).
6. `0006-simulated-clock-mttr.md` — MTTR on a defined sim clock, human wait excluded, drivers always reported.

### 17.4 Designed error states (graceful *and* visible)
| Failure | Backend behavior | UI state |
|---|---|---|
| Groq down / all retries fail | `ESCALATE_HUMAN` recommendation, `error{LLM_UNAVAILABLE}` | Calm amber card: "LLM unavailable — escalated to on-call engineer" |
| Malformed tool call | salvage → retry → corrective message (7.5) | Trace line: "model returned invalid tool call — retrying" |
| Hindsight recall fails | continue without memory, `error{MEMORY_UNAVAILABLE}` | Memory panel: "Memory unavailable — reasoning from evidence only" |
| Hindsight retain fails | outbox + background retry (6.2) | Memory Browser row badge: "pending sync" |
| Guardrail violation | structured `error` event, action not executed | Inline message on the control that caused it |
| Backend unreachable | — | Header banner with retry countdown |

### 17.5 Reproducibility
- `make setup` (uv sync + npm ci), `make dev`, `make test`, `make lint`, `make typecheck`, `make eval` / `make eval-quick`, `make seed`, `make demo`, `make reset`.
- `docker compose up` runs backend + frontend with `.env`; verified from a fresh clone before submission.
- FastAPI's OpenAPI docs at `/docs`, linked from the README — typed contracts, visible.

---

## 18. Q&A Armor (every answer cites a measurement or a design fact)

Bracketed values are filled from the committed eval report before the demo; if a number isn't measured, the answer says so.

| Judge asks | Answer |
|---|---|
| "How is this different from RAG over runbooks?" | RAG retrieves documents; we accumulate **outcomes**. Memory holds which fix worked or failed on which signature, carries it across machines, and records when a fix wears off — experience, not text. |
| "Is the matching just string matching?" | No. Queries are paraphrased and contain no log text, machine IDs or versions. Hindsight runs semantic, keyword, graph and temporal retrieval (TEMPR) and reranks with a cross-encoder. In our design spike the correct past incident ranked #1 in 5/5 queries with ≥4× the reranker score of any false match (14.1); across the full eval, recall@1 is [x] (11.3). |
| "What if the LLM hallucinates?" | Every output is Pydantic-validated against a 6-action whitelist; tool arguments are validated before execution (we caught a model silently rewriting an invalid machine ID); malformed calls retry, then fall back to a second model; the final fallback is escalation to a human, never an unbounded action. Every decision is in the Langfuse trace. |
| "What happens at 10,000 episodes?" | Not measured at that scale. Measured: recall takes 0.13–0.16 s at small scale (14.1), and our match rule is relative to the top result, so it doesn't depend on absolute scores drifting as the bank grows. Retrieval scaling is Hindsight's engine. [If the COULD load test runs: result at 500 episodes.] |
| "Would a company deploy this?" | The agent sees only six typed adapter methods; implement them against your observability and ops stack and nothing else changes. The workflow — agent recommends, on-call engineer approves — is how incident tools are deployed today. |
| "Why one agent, not multi-agent?" | One protagonist, one memory bank, one measurable learning curve. Multi-agent would fragment the memory story, and the eval shows the single loop learns: [paired ON−OFF accuracy gain, CI]. |
| "Is the improvement real or scripted?" | Run `make eval`: paired memory ON vs OFF, same incidents, 3 seeds, 95% CIs, committed report [link]. Or trigger any incident yourself in the simulator. |

---

## 19. Priority Stack & Anti-Scope

**MUST (win conditions)**
1. Core loop works flawlessly (Phases 1–4), adapter boundary from day one
2. Evaluation suite + committed report (Phase 5)
3. Seeded 6-month history, realistic and contradictory (Phase 6)
4. Rehearsed 3.5-min demo incl. sabotage beat + memory toggle
5. Q&A answers memorized, every claim traceable to a measurement
6. README: architecture diagram, eval table, CI badge

**SHOULD (strong signals)** — pytest suite + CI (built continuously from Phase 1) · Langfuse trace ready for Q&A · 6 ADRs · one-command `make demo` / docker compose

**COULD (if time remains)** — 500-episode load test (reported as measured) · offline fallback laptop · live failure-handling demo · memory-mode comparison (recall only vs recall + mental model) in the eval

**WON'T** — auth / multi-user / multi-tenancy · real Datadog/K8s integrations · more than 4 incident types · a second agent, second memory or MCP server · deployment beyond docker compose

---

*End of specification. When something is ambiguous, choose the option that makes memory (retain → recall → visibly better outcomes) most prominent in the UI and the demo.*
