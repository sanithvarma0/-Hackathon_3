# MemoryOps — Build Specification v1.2

> **v1.2 changes (from v1.1, after reviewing reference repos — see TECH_STACK.md "Reference Repositories"):** "Incident Patterns" mental model promoted from stretch to core (Phase 3), configured from Vectorize's own ops bank template; episode record gains an `INVESTIGATION PATH` section (what evidence was decisive) to power `recall_hints`; Groq failure handling made concrete (`tool_use_failed` salvage, `include_reasoning: false`, fallback model verified at spike time); `simulate.py` runs each mode on its own throwaway bank and reports investigation efficiency; added a differentiation note against the official Hindsight cookbook demos.
>
> **v1.1 changes (from v1.0):** grounded all Hindsight calls in the real `hindsight-client` v0.10 SDK; added a Phase 0.5 spike; added a simulated clock so MTTR is measured, not invented; added a `verify` step (without it a trap fix looks like success); added a second memory touchpoint (`recall_hints`) so memory makes the agent *faster*, not just more accurate; removed the LLM-callable memory tool (it leaked memory into Memory-OFF runs); defined Memory-OFF semantics, demo reset, bank switching, SSE replay, Hindsight outage handling, full action/effect matrix, custom-builder classification rules, and a submission checklist. All headline numbers are now **targets to be replaced with measured values** (Section 14).

---

## 1. Project Overview

**MemoryOps** is a self-learning production incident commander. It simulates a factory floor (5 machines) where incidents occur. A single AI agent detects, investigates, and diagnoses incidents — and critically, **remembers every past incident via Hindsight**, so repeated incident classes get resolved faster and more accurately over time.

**The demo thesis:** the first incident of a class is slow and uncertain; later incidents of the same class — on a *different* machine, with *different* log wording — are fast, confident, and cite the earlier incident. Judges can trigger incidents themselves and verify the learning is real, not scripted.

> Target shape (placeholder until measured): incident #1 → ~10 sim-min MTTR, ~0.6 confidence; incident #5 of the same class → ~3 sim-min MTTR, ~0.9 confidence. Replace with real numbers from Phase 5 simulation runs.

**This is NOT a chatbot.** The judge interacts with a simulated production environment (buttons, sliders, dashboards). The agent runs autonomously in response to environmental events. The judge's "test" is: trigger an incident → watch the agent investigate → approve an action → trigger the same class again → watch it be faster.

**Fit to the problem statement:** "Incident Response Agent" (Engineering & DevOps category), applied to manufacturing / OT. Memory is the product: the same agent with memory OFF is measurably worse, live, on stage.

**Differentiation (Innovation = 30%).** Judges from Vectorize will know the official cookbook demos — ClaimsIQ (claims triage, "confused rookie → seasoned expert") and CableConnect (CSR copilot that learns from rejections). Both learn from a **human telling the agent it was wrong**. MemoryOps learns from **the environment's delayed consequences**: nobody tells the agent RESTART was wrong — the machine re-degrades 90 seconds later, the agent notices, and that becomes memory. Plus: judges inject incidents themselves into a live simulator (not a fixed scenario queue), the agent generalizes a signature across *different machines and wording*, and improvement is measured in an operational KPI (MTTR), not just "right/wrong". Say this explicitly in the README and demo.

---

## 2. Tech Stack (FROZEN — see TECH_STACK.md for versions and rationale)

| Layer | Choice | Notes |
|---|---|---|
| Agent runtime | **LangGraph** (raw `StateGraph`) | Single agent. No `create_react_agent`, no DeepAgents, no multi-agent. |
| Human-in-the-loop | **LangGraph `interrupt()` + `InMemorySaver`** | `thread_id = incident_id`; resume with `Command(resume=...)`. |
| Memory | **Hindsight Cloud** via `hindsight-client` (Python, v0.10.x) | `https://api.hindsight.vectorize.io`. The ONLY memory system. Promo `MEMHACK99`. |
| LLM | **Groq** via the OpenAI-compatible endpoint | Primary `openai/gpt-oss-120b`, fallback `qwen/qwen3-32b`. |
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
│                   │mental    │ │qwen3-32b │ │          │     │
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
├── README.md                   # final deliverable (judges read this)
├── .env.example
├── pyproject.toml              # backend deps (pinned via uv.lock)
├── backend/
│   ├── main.py                 # FastAPI app, routes, SSE endpoint
│   ├── config.py               # pydantic-settings: env vars, SIM_SPEED, thresholds
│   ├── events.py               # event bus: ring buffer, SSE fan-out, Last-Event-ID replay
│   ├── llm.py                  # Groq client (OpenAI SDK) + retry + fallback + Langfuse
│   ├── guardrails.py           # the 6 guardrails
│   ├── schemas.py              # Pydantic models: Recommendation, ActionResult, API payloads
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
│       └── metrics.py          # MTTR / confidence / hit-rate / tool-call tracker
├── tests/
│   ├── test_simulator.py       # effects matrix, re-degradation, classifier
│   ├── test_guardrails.py
│   └── test_graph_routing.py   # routing with fake LLM + fake memory
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
    ├── simulate.py             # headless N-incident runs (auto-approve) → metrics
    ├── seed_history.py         # 6-month history into the *seeded* bank
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

| ID | Profile | Line | Gateway |
|---|---|---|---|
| M1 | CNC vertical mill — Haas VF-4, commissioned 2021 | A (1st) | GW-A |
| M2 | CNC lathe — Mazak QT-250, commissioned 2019 | A (2nd) | GW-A |
| M3 | Robotic welding cell — Fanuc ARC Mate 100iD, commissioned 2022 | A (3rd) | GW-A |
| M4 | 5-axis machining center — DMG Mori NVX 5080, commissioned 2020 | B (1st) | GW-B |
| M5 | Injection molding press — Engel victory 200, commissioned 2018 | B (2nd) | GW-B |

"Downstream" = next machines on the same line. Network failures cascade to machines on the same gateway.

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
| `network_failure` | packet loss 8–15%, latency spikes; **sudden** onset | affected gateway, cascade to 1–2 machines on it | `RESTART_GATEWAY` |
| `resource_exhaustion` | memory climbing over 3+ days in history; OOM-kill events | machine, climb rate | `CLEAR_CACHE` |

**Action whitelist (exhaustive):** `ROLLBACK_CONFIG`, `RESTART_MACHINE`, `RECALIBRATE_SENSOR`, `RESTART_GATEWAY`, `CLEAR_CACHE`, `ESCALATE_HUMAN`.

### 5.5 Effects Matrix (complete — `execute_action` implements exactly this)

| Type ↓ / Action → | ROLLBACK_CONFIG | RESTART_MACHINE | RECALIBRATE_SENSOR | RESTART_GATEWAY | CLEAR_CACHE |
|---|---|---|---|---|---|
| `config_regression` | **full** | partial → 65–75%, **re-degrades after 60–120 sim-s** | none | none | none |
| `sensor_drift` | none (config never wrong) | none | **full** | none | none |
| `network_failure` | none | none (machine fine, network broken) | none | **full** (all affected machines) | none |
| `resource_exhaustion` | none | partial → ~90%, **re-degrades after 120–150 sim-s** | none | none | **full** |

- `ESCALATE_HUMAN` (any type): incident closed as `escalated`; a fixed `ESCALATION_PENALTY_SIM_S` (default 1800) is added to MTTR to reflect handing off to an on-call engineer.
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

**IGNORE:** the `/ignore` endpoint applies the cascade immediately (throughput drops a further 10–20% and 1–2 downstream machines degrade) and the incident stays open awaiting action. No automatic 8-minute timer in demo mode — a judge reading the screen should never trigger a cascade by accident. (Optional `AUTO_CASCADE_SIM_S` for non-demo runs.)

### 5.7 Custom Incident Builder (structured, no free text)

Input: `{machine, config_changed: bool, minutes_before: 1..60, throughput_delta: -50..-5, error_rate: 0..20, temperature: normal|high, calibration: fresh|old, network: normal|degraded, memory_trend: flat|climbing}`.

Classification (first match wins; the agent never sees the result):
1. `network == degraded` → `network_failure`
2. `config_changed and 5 <= minutes_before <= 30` → `config_regression`
3. `calibration == old and not config_changed` → `sensor_drift`
4. `memory_trend == climbing` → `resource_exhaustion`
5. otherwise → `ambiguous`: no fix works; the only correct action is `ESCALATE_HUMAN` (tests the agent's willingness to say "I don't know").

### 5.8 Simulator API (internal, called by FastAPI + agent tools)

```python
class Simulator:
    def get_machine_metrics(machine_id) -> dict          # current snapshot
    def get_metric_history(machine_id, metric, window_hours) -> list[dict]
    def get_recent_events(machine_id, window_min) -> list[dict]
    def get_error_logs(machine_id, window_min) -> list[str]
    def trigger_incident(type=None, machine=None, custom=None) -> Incident
    def execute_action(incident_id, action) -> ActionResult  # per effects matrix
    def ignore(incident_id) -> None                      # cascade
    def tick() -> None                                   # advance sim time (background task)
    def machine_status_all() -> list                     # dashboard
    def reset() -> None
```

`execute_action` returns `{effect, recovery_pct, re_degrade_after_sim_s | None}`. Partial effects recover the machine, then the engine re-degrades it after the delay.

---

## 6. Hindsight Memory Layer (Phase 2)

### 6.1 Banks

| Bank ID | Purpose |
|---|---|
| `memoryops-live` | Starts empty. Used for the main demo so the first incident is genuinely cold. |
| `memoryops-seeded` | Pre-loaded by `seed_history.py` with ~6 months of synthetic history (~25 episodes). Used for the "recalls something from weeks ago" beat. |

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

`DIAGNOSIS` is the **agent's** diagnosis, never the simulator's ground-truth type.

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

**Query construction — deliberately paraphrased, never keyword-copied** (proves semantic matching):

```
"machine showing {onset} throughput decline with {key signals, paraphrased},
config {changed/unchanged} recently, calibration {age}"
```

Paraphrasing is done by a small deterministic synonym table in `memory/render.py` (e.g. "servo timeout" → "axis drive not responding in time"), not an LLM, so it's reproducible.

**Recall call:**

```python
resp = client.recall(
    bank_id=bank_id, query=query,
    types=["world", "experience", "observation"],
    budget="mid", max_tokens=4096,
    include_source_facts=True,
)
```

**Match scoring** (verified against SDK: each result has `scores.final`, `scores.reranker` (0–1), `scores.semantic` (cosine 0–1, null if not found by the semantic arm), `scores.keyword`):
- Group raw facts (`world`/`experience`) by `metadata.incident_id` → one **incident match** per past incident; match score = max `scores.semantic` over its facts (fall back to `scores.reranker` if semantic is null).
- Keep matches with score ≥ `MEMORY_MATCH_THRESHOLD` (initial **0.7**, **calibrated in Phase 0.5** — Hindsight documents that scores are relative per query, so this must be tuned against observed values).
- `observation` results are shown separately as **"Learned patterns"** (consolidated beliefs like "restart only gives temporary relief for config regressions"). Consolidation runs in the background after retain, so observations may lag a few seconds; the demo does not depend on them being instant.

Output to state: `memory_results = [{incident_id, score, summary, final_action, outcome}]`, `learned_patterns = [str]`.

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
  → act ⏸                 (interrupt(); judge picks action; executes)
  → route_after_act:
       ESCALATE_HUMAN ─────────────────────────────→ learn → END
       otherwise → verify (watch VERIFY_WINDOW_SIM_S, default 180)
  → route_after_verify:
       full_recovery ──────────────────────────────→ learn → END
       partial/no_effect and attempt_count < 3 ────→ record_lesson → investigate
       partial/no_effect and attempt_count == 3 ───→ learn (escalated) → END
```

- `recall_hints`: recall on the alert; hints like "in similar past incidents, recent config deploys were the root cause" are injected into the investigate prompt.
- `investigate`: LLM tool-calling loop over the 4 tools. Max **10 tool calls** (guardrail 2). Every call and result streams as SSE. Ends with an `investigation_summary`. On retry it also sees the previous attempts ("RESTART_MACHINE → recovered to 70%, re-degraded after 90s").
- `search_memory`: deterministic recall (6.3).
- `decide`: LLM → `Recommendation` (Pydantic, whitelist). Must cite matched incident IDs when memory informed the decision.
- `act`: **no LLM**. `interrupt({"recommendation": ...})` pauses the graph; state lives in `InMemorySaver` keyed by `thread_id=incident_id`. `POST /api/incident/{id}/action` resumes with `Command(resume={"action": ...})`. The judge may pick *any* whitelisted action (this is the sabotage beat). In `auto_approve` mode (simulation scripts), the recommended action is applied without interrupting.
- `verify`: **no LLM**. Polls metrics over the verify window; declares `full_recovery` only if recovery holds for the whole window. This is what catches trap fixes.
- `record_lesson`: renders + retains a partial lesson (6.2), then loops.
- `learn`: renders + retains the episode, writes metrics, closes the incident, emits `memory_written` and `metrics_updated`.

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
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    cited_incidents: list[str] = []     # must be ⊆ memory_results incident_ids
    actions_known_to_fail: list[str] = []
```

**Confidence:** store both the LLM's self-reported `confidence` and a `calibrated_confidence` (initial formula, to be tuned after simulations: blend of LLM confidence, evidence coverage, and the best memory match score whose recorded successful action equals the recommendation). The UI shows the calibrated value; both are logged.

### 7.5 LLM Client (llm.py)

- `openai` SDK pointed at Groq (`base_url="https://api.groq.com/openai/v1"`), imported via `from langfuse.openai import OpenAI` so every call is traced automatically.
- Primary `openai/gpt-oss-120b`; retry ×2 with backoff (1 s, 2 s); then fallback model (`LLM_FALLBACK_MODEL`, default `qwen/qwen3-32b`) with the same retries; a **total time budget per call** (default 30 s) caps retries; on total failure → `ESCALATE_HUMAN` recommendation with reasoning "LLM unavailable" (graceful degradation, never crash).
- **Fallback model availability:** Groq's catalogue changes; `spike_groq.py` lists `GET /models` and confirms the fallback is served (candidates seen in reference code: `qwen/qwen3-32b`, `qwen/qwen3.6-35b-a3b`, `openai/gpt-oss-20b`). It's an env var, so swapping is a config change.
- **Malformed tool calls** (organizers explicitly warned). Groq rejects them with **HTTP 400, `error.code == "tool_use_failed"`, and the raw model output in `error.failed_generation`** (same handling as Hindsight's own Groq provider). Order: (1) try to salvage — parse `failed_generation` as `{name, arguments}` and, if it validates against a known tool, use it; (2) else retry once; (3) else inject a corrective system message ("Your last tool call was malformed: … Call one of: …"). JSON-parse failures of `arguments` and unknown tool names go through the same path.
- **Reasoning text:** send `extra_body={"include_reasoning": False}` for reasoning models, and still strip any `<think>…</think>` block from content before parsing (belt and braces).
- Structured output for `decide`: request JSON, validate with `Recommendation`; on validation failure → one corrective retry (guardrail 1/3).

---

## 8. Guardrails (guardrails.py)

1. **Pydantic validation** on all LLM outputs (`Recommendation`; `cited_incidents` must be real matched IDs).
2. **Max 10 tool calls** per investigation → forced conclusion.
3. **Action whitelist** (6 actions, 5.4) — invalid actions rejected + corrective retry; never executed.
4. **Dependency retry + fallback** — LLM retry/model fallback (7.5); Hindsight failures degrade to "memory unavailable" for recall and outbox-retry for retain (6.2).
5. **Simulator state validation** — can't trigger on an already-degraded machine; **one active incident at a time** (keeps the demo legible); can't act on a closed incident. Violations return a structured `error` event, not a 500.
6. **Memory match threshold** — `MEMORY_MATCH_THRESHOLD` (initial 0.7, calibrated in Phase 0.5); below it, results are not shown as "matches".

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
POST /api/admin/reset                 # {bank: "live"|"seeded", wipe_memory: bool}
```

**Why one global stream:** the frontend opens it once at page load, so there is no race between "POST created the incident" and "client subscribed". Every event carries `incident_id` (or null). The event bus keeps a ring buffer (last ~2000 events) with monotonically increasing IDs; `EventSource` reconnects replay from `Last-Event-ID`.

**SSE event types (contract with frontend):**

```
state_changed        {machines: [...], oee, alerts}               # every ~1 s real
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

## 10. Frontend (Phase 4)

Next.js App Router + Tailwind + shadcn/ui + Recharts. **Dark theme ops aesthetic.**

### 10.1 Layout

- **Header:** "MemoryOps — Self-Learning Production Incident Commander" · **🧠 MEMORY [ON/OFF]** (prominent) · active bank badge (live / seeded) · KPI strip (OEE, throughput, active alerts) · sim clock.
- **Dashboard (`/`):** machine cards M1–M5 (status color, throughput, sparkline). Left rail: 4 predefined buttons, **CUSTOM BUILDER** (modal), **SURPRISE ME**, reset (behind a confirm).
- **Incident panel** (slides in on the dashboard when an incident is active — the judge never leaves the page):
  - **Investigation Trace** — terminal-style live log of tool calls/results; memory events highlighted in a distinct color. Replaces a chat window.
  - **Memory panel** — hints, matched episodes with score badges, learned patterns. Memory OFF → "Memory disabled — agent reasoning from evidence only."
  - **Recommendation card** — action, confidence bar, reasoning, cited incidents (clickable → Memory Browser), actions known to fail (red).
  - **Action buttons** — the 6 whitelisted actions (recommended one highlighted) + **IGNORE**.
- **Memory Browser (`/memory`):** all episodes and lessons; expand to see the exact text retained to Hindsight and its metadata.
- **Learning (`/learning`, Recharts):** MTTR per incident, confidence progression, tool calls per incident, memory hit rate, recommendation accuracy — each split by memory ON/OFF, filterable by diagnosis. Headline: "The agent is learning."

### 10.2 Custom Builder Modal

Machine (M1–M5) · config_changed [Yes/No] · minutes_before slider · throughput delta slider · error rate slider · temperature [Normal/High] · calibration [Fresh/30+ days] · network [Normal/Degraded] · memory trend [Flat/Climbing] · **[RUN CUSTOM INCIDENT]**. No free-text field anywhere.

### 10.3 Behavior

- Initial load via `/api/state`; afterwards everything is SSE (`state_changed` ticks replace polling).
- `re_degradation` animates the machine card flashing down again.
- Trigger buttons are disabled while an incident is active (mirrors guardrail 5).

---

## 11. Evaluation Tracker (eval/metrics.py)

```sql
metrics(incident_id TEXT, diagnosis TEXT, true_type TEXT, memory_enabled INTEGER,
        mttr_sim_s INTEGER, agent_time_real_s REAL, human_wait_sim_s INTEGER,
        tool_calls INTEGER, attempts INTEGER,
        investigation_efficiency REAL,      -- decisive tool calls / total tool calls
        llm_confidence REAL, calibrated_confidence REAL,
        memory_hit INTEGER,                 -- any match ≥ threshold
        recommendation_correct INTEGER,     -- agent's FIRST recommendation == correct fix
        first_time_right INTEGER,           -- first EXECUTED action == full recovery
        ts INTEGER)
```

`recommendation_correct` measures the agent; `first_time_right` depends on what the judge clicked — both are tracked, charts default to the agent-side metric.

**Acceptance test that matters most:** `scripts/simulate.py` runs ≥5 incidents of a class on random machines in `auto_approve` mode, with memory ON and again with memory OFF. Each mode runs on its **own throwaway bank** (`memoryops-sim-<run_id>-<mode>`, deleted afterwards) so runs never contaminate each other or the demo banks (pattern from the cookbook's deliveryman benchmark). Output: a JSON results file + a markdown summary table (per incident: MTTR, tool calls, efficiency, confidence, correct?). With memory ON, MTTR and tool calls must trend down and confidence up; with memory OFF they must stay flat. If not, the demo is broken — fix before building UI polish.

---

## 12. Demo Script (3.5 min — build everything to serve this)

Numbers in brackets are placeholders, filled from Phase 5 measurements.

1. **0:00** Healthy factory, `memoryops-live` bank (empty). "MemoryOps runs production, and it gets better at its job every day."
2. **0:15** Trigger config regression on M3 → cold investigation trace streams (many tool calls) → [~0.6] confidence → judge applies ROLLBACK → verify → recovery → **memory written — the episode appears in Memory Browser.**
3. **1:15** Trigger config regression again — **on M4, different log phrasing** → hints steer investigation (fewer tool calls) → match [≥0.7] cites INC-001 → resolved in [~3 sim-min]. "It matched the signature, not the machine."
4. **1:45** Sabotage: trigger config regression, apply **RESTART** instead → partial recovery → **re-degrades on stage** → lesson written → agent re-investigates, recommends rollback, lists RESTART under "known to fail".
5. **2:15** Sensor drift → agent does NOT replay "rollback" (different signature) → recommends recalibration. Discrimination proven.
6. **2:40** Learning tab: MTTR curve [cold → warm], tool calls falling, confidence rising.
7. **2:55** Toggle MEMORY OFF, trigger config regression → cold behavior again. Toggle ON → instant match. Live before/after.
8. **3:15** (Optional) Switch to seeded bank: agent cites an incident from [6 weeks] ago. Close: "Day one it's a rookie. Every incident makes it better. That's what memory is for."

---

## 13. Build Order & Acceptance Criteria

**Phase 0 — Scaffold.** Repo layout (Section 4), `pyproject.toml` + lockfile, `.env.example` (Section 15), FastAPI skeleton with `/api/health`, Next.js skeleton. ✅ `uv sync` (or `pip install -e .`) + `uvicorn backend.main:app` boots; `/api/health` reports each dependency.

**Phase 0.5 — Spikes (first 1–2 hours of Phase 2 work, do before building nodes).**
- `spike_hindsight.py`: create bank, retain 3 synthetic episodes (2 config regression with different wording, 1 sensor drift), recall with a paraphrased query. ✅ Record: retain latency; recall-immediately-after-retain works; score distributions (`semantic`, `reranker`, `final`) for true vs false matches → **set `MEMORY_MATCH_THRESHOLD`**; observation lag.
- `spike_groq.py`: list available models; tool-calling round trip on primary + fallback; provoke a malformed tool call and confirm the `tool_use_failed` / `failed_generation` shape; confirm `include_reasoning: false` suppresses reasoning. ✅ Record: fallback model choice, error shapes, typical latency.

**Phase 1 — Simulator.** ✅ `pytest tests/test_simulator.py`: each of 4 types triggers on random machines; metrics degrade per taxonomy; every cell of the effects matrix (5.5) produces the specified effect incl. re-degradation timing; IGNORE cascades; custom builder maps to the right class incl. `ambiguous`; sim clock math; SQLite persists everything.

**Phase 2 — Agent.** ✅ `demo_dryrun.py` headless: config regression → real tool calls → valid recommendation → action executes → verify → episode retained (visible in Hindsight Cloud UI) → reworded same-type incident on another machine → recall returns prior episode above threshold → fewer tool calls, higher confidence, reasoning cites INC-ID → trap-fix loop works (restart → re-degrade → lesson retained → re-investigate → rollback) → memory OFF run shows no memory events.

**Phase 3 — API + SSE + Mental Model.** ✅ Incident Patterns mental model created at startup and readable after a few incidents; all endpoints work; one global SSE stream carries the full event contract; reconnect replays via Last-Event-ID; human-in-the-loop resume via action endpoint; guardrail violations and dependency failures emit `error` events instead of crashing.

**Phase 4 — Frontend.** ✅ Full demo script (Section 12) runs end-to-end in the browser, including memory toggle, re-degradation animation, and reset.

**Phase 5 — Measure & Polish.** ✅ `simulate.py` acceptance test (Section 11) passes; real numbers replace placeholders in Sections 1, 12 and README; Learning charts correct; Memory Browser shows real retained content; Langfuse traces present; seeded bank loaded; README with architecture diagram, how to run, and "How Hindsight memory is used" (required deliverable). Stretch: remaining two incident types polished; memory-mode comparison in `simulate.py` (recall only vs recall + mental model).

**Incremental order inside phases:** config regression end-to-end first (sim → agent → API → UI), then sensor drift, then network failure and resource exhaustion, then custom builder, then IGNORE cascade.

---

## 14. Values To Be Measured (fill in after simulations)

| Item | Initial value | Set by |
|---|---|---|
| `MEMORY_MATCH_THRESHOLD` | 0.7 on `scores.semantic` | Phase 0.5 spike |
| `SIM_SPEED` | 10 | Phase 4 demo rehearsal |
| `VERIFY_WINDOW_SIM_S` | 180 | Phase 1 (must exceed max re-degrade delay) |
| `ESCALATION_PENALTY_SIM_S` | 1800 | Phase 1 |
| Calibrated-confidence formula weights | TBD | Phase 5 simulation |
| Headline MTTR / confidence numbers | placeholders | Phase 5 simulation |

---

## 15. Configuration (.env.example)

```bash
# Hindsight
HINDSIGHT_BASE_URL=https://api.hindsight.vectorize.io
HINDSIGHT_API_KEY=
HINDSIGHT_BANK_LIVE=memoryops-live
HINDSIGHT_BANK_SEEDED=memoryops-seeded
# Groq
GROQ_API_KEY=
GROQ_BASE_URL=https://api.groq.com/openai/v1
LLM_PRIMARY_MODEL=openai/gpt-oss-120b
LLM_FALLBACK_MODEL=qwen/qwen3-32b
# Langfuse
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
# Simulator / agent
SIM_SPEED=10
VERIFY_WINDOW_SIM_S=180
ESCALATION_PENALTY_SIM_S=1800
MEMORY_MATCH_THRESHOLD=0.7
MAX_TOOL_CALLS=10
MAX_ATTEMPTS=3
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

---

*End of specification. When something is ambiguous, choose the option that makes memory (retain → recall → visibly better outcomes) most prominent in the UI and the demo.*
