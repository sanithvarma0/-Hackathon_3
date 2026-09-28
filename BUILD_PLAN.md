# MemoryOps — Build Specification v1.0

## 1. Project Overview

**MemoryOps** is a self-learning production incident commander. It simulates a factory floor (5 machines) where incidents occur. A single AI agent detects, investigates, and diagnoses incidents — and critically, **remembers every past incident via Hindsight**, so repeated incident classes get resolved dramatically faster over time.

**The demo thesis:** First incident → 22 min, 68% confidence. Fifth similar incident → 40 seconds, 96% confidence. Judges can trigger incidents themselves and verify the learning is real, not scripted.

**This is NOT a chatbot.** The user/judge interacts with a simulated production environment (buttons, sliders, dashboards). The agent runs autonomously in response to environmental events. The judge's "test" is: trigger an incident → watch the agent investigate → approve an action → trigger the same class again → watch it be faster.

---

## 2. Tech Stack (FROZEN — do not substitute)

| Layer | Choice | Notes |
|---|---|---|
| Agent runtime | **LangGraph** (raw, no wrappers) | Single agent. No LangChain `create_agent`, no DeepAgents, no multi-agent. |
| Memory | **Hindsight Cloud** | Single memory bank. Promo `MEMHACK99` for credits. The ONLY memory system. |
| LLM | **Groq** | Primary `openai/gpt-oss-120b`, fallback `qwen/qwen3-32b`. Must handle function-call errors with retry. |
| Backend | **FastAPI** + SSE | Python 3.11+. |
| Database | **SQLite** | Simulator state only. No Postgres, no ChromaDB, no FAISS. |
| Observability | **Langfuse** cloud free tier | Trace all LLM + tool calls. |
| Frontend | **Next.js + Tailwind + shadcn/ui + Recharts** | Ops dashboard. No chat UI. |
| Guardrails | Custom (6 rules, Section 9) | Pydantic validation everywhere. |

**Explicitly rejected** (do not add): multi-agent orchestration, MCP/A2A/ACP, DeepAgents, Mem0/Zep/Cognee, any second memory, any vector DB, semantic layer, WrenAI/GenBI, free-text incident input.

**Reference implementations:**
- FastAPI + LangGraph + SSE pattern: https://github.com/JoshuaC215/agent-service-toolkit
- Hindsight docs + Python SDK: https://hindsight.vectorize.io (see Clients → Python, and Frameworks & SDKs → LangGraph)

---

## 3. System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    NEXT.JS FRONTEND                          │
│  Dashboard │ Incident View │ Memory Browser │ Learning Tab   │
│  [Memory ON/OFF toggle — prominent, in header]               │
└──────────────────────────┬──────────────────────────────────┘
                           │ SSE (streaming events)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                        FASTAPI                               │
│                                                              │
│  ┌───────────────┐        ┌──────────────────────────────┐ │
│  │ SIMULATOR      │        │ AGENT (LangGraph)            │ │
│  │ state machine  │◀─────▶│                               │ │
│  │ + incident     │        │ investigate → search_memory   │ │
│  │ generator      │        │    → decide → act → learn     │ │
│  │ (SQLite)       │        │    (+ retry loop on failure)  │ │
│  └───────────────┘        └──────┬───────────────────────┘ │
│                                  │                           │
│                    ┌─────────────┼─────────────┐           │
│                    ▼             ▼             ▼           │
│              ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│              │ HINDSIGHT │  │  GROQ    │  │ LANGFUSE │      │
│              │ retain/  │  │ gpt-oss- │  │  traces  │      │
│              │ recall   │  │ 120b →   │  │          │      │
│              └──────────┘  │ qwen3-32b│  └──────────┘      │
│                            └──────────┘                     │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Repository Structure

```
memoryops/
├── BUILD_PLAN.md              # this document
├── README.md                  # final deliverable (judges read this)
├── .env.example
├── backend/
│   ├── main.py                # FastAPI app, routes, SSE endpoint
│   ├── config.py              # settings, env vars
│   ├── llm.py                 # Groq client + retry + fallback
│   ├── guardrails.py          # 6 guardrail implementations
│   ├── agent/
│   │   ├── graph.py           # LangGraph definition
│   │   ├── state.py           # AgentState TypedDict
│   │   ├── nodes/
│   │   │   ├── investigate.py
│   │   │   ├── search_memory.py
│   │   │   ├── decide.py
│   │   │   ├── act.py
│   │   │   └── learn.py
│   │   ├── tools.py           # tool registry the LLM sees
│   │   └── prompts.py         # all system prompts
│   ├── simulator/
│   │   ├── engine.py          # state machine, degradation, recovery
│   │   ├── incidents.py       # incident templates + noise generation
│   │   └── db.py              # SQLite models + init
│   ├── memory/
│   │   └── hindsight.py       # Hindsight client wrapper
│   └── eval/
│       └── metrics.py         # MTTR / confidence / hit-rate tracker
├── frontend/
│   ├── (Next.js app — see Section 10)
│   └── ...
└── scripts/
    ├── seed_history.py        # optional: seed 6-month history via Hindsight
    └── demo_dryrun.py         # headless end-to-end test
```

---

## 5. Simulator Design (Phase 1)

### 5.1 State Model

SQLite tables:

```sql
machines(id TEXT PRIMARY KEY,          -- "M1".."M5"
         name TEXT, profile TEXT,      -- "CNC mill, commissioned 2021"
         status TEXT,                  -- healthy | degraded | critical
         throughput REAL, oee REAL)

machine_history(ts INTEGER, machine_id TEXT,
                throughput REAL, oee REAL, error_rate REAL)

events(ts INTEGER, machine_id TEXT,
       event_type TEXT, detail TEXT)   -- config_deployed, calibration, deploy, etc.

incidents(id TEXT PRIMARY KEY,         -- "INC-001"
          type TEXT, machine_id TEXT,
          signature TEXT,              -- JSON blob
          status TEXT,                 -- open | resolved | escalated
          opened_ts INTEGER, resolved_ts INTEGER,
          resolution_action TEXT, mttr_seconds INTEGER)

actions_log(ts INTEGER, incident_id TEXT, action TEXT,
            effect TEXT,               -- full_recovery | partial_recovery | no_effect
            recovery_pct REAL, re_degraded_after_sec INTEGER NULL,
            executed_by TEXT)          -- judge | agent
```

**Healthy baseline:** each machine throughput 95–99%, OEE 88–93%, error_rate 0–2%. Add slow ambient jitter (small random walk) so the dashboard looks alive.

### 5.2 Incident Taxonomy

Each incident type = **stable signature + noisy surface**. Build all four, but Config Regression and Sensor Drift are the polished hero scenarios.

| Type | True Signal (signature) | Surface Noise (varies every occurrence) | Correct Fix | Trap Fix (partial/none) |
|---|---|---|---|---|
| `config_regression` | config deployed 5–20 min before onset; **gradual** throughput decline 25–45% | machine (random), exact drop %, log phrasing variant, onset delay | `ROLLBACK_CONFIG` → full recovery | `RESTART_MACHINE` → recovers to ~65–75%, **re-degrades after 60–120s** |
| `sensor_drift` | calibration age > 30 days; sensor variance high; config **unchanged** | machine, drift magnitude, phantom temperature alarms | `RECALIBRATE_SENSOR` → full recovery | `ROLLBACK_CONFIG` → no effect (config was never wrong) |
| `network_failure` | packet loss 8–15% between nodes; **sudden** onset; latency spikes | affected machine, cascade pattern to 1–2 downstream machines | `RESTART_GATEWAY` → full recovery | `RESTART_MACHINE` → no effect (machine fine, network broken) |
| `resource_exhaustion` | memory climbing over 3+ days in history; OOM kill events | machine, climb rate | `CLEAR_CACHE` → full recovery | `RESTART_MACHINE` → partial, re-degrades in ~10 min |

**Action whitelist (exhaustive):**
`ROLLBACK_CONFIG`, `RESTART_MACHINE`, `RECALIBRATE_SENSOR`, `RESTART_GATEWAY`, `CLEAR_CACHE`, `ESCALATE_HUMAN`.

### 5.3 Incident Generation

```python
INCIDENT_TEMPLATES = {
  "config_regression": {
     "signature":  {"config_changed_before_min": (5,20),
                    "onset": "gradual", "throughput_drop_pct": (25,45)},
     "log_variants": [
        "servo timeout on axis {axis}",
        "cycle time +{pct}% (nominal exceeded)",
        "control loop jitter, position error {err}mm"],
  }, ...
}

def generate_incident(type, machine=None, severity=None, custom_overrides=None):
    """1. Pick machine (random healthy one if not given).
       2. Sample signature params within ranges.
       3. Sample noise: log phrasing variant, drop %, timestamps.
       4. Write to machines/events/incidents tables.
       5. Start degradation timer (gradual = step down over ~60s)."""
```

**Degradation dynamics:** `config_regression` degrades gradually (visible decline over 30–60 s in the dashboard). `network_failure` is instant. If an incident is open and **no action is taken within 8 minutes** (simulated time can be accelerated), cascade: throughput drops further and 1–2 downstream machines degrade (the IGNORE consequence).

**Custom incident builder input** (structured, no free text): `{machine, config_changed: bool, minutes_before: int, throughput_delta: slider(-50..-5), error_rate: slider, temperature: normal|high, calibration: fresh|old, network: normal|degraded}`. Simulator classifies internally by strongest signal and applies that type's fix semantics — but the agent only sees raw signals/logs and must reason.

### 5.4 Simulator API (internal, called by FastAPI + agent tools)

```python
class Simulator:
    def get_machine_metrics(machine_id) -> dict      # throughput, oee, error_rate, status
    def get_recent_events(machine_id, window_min) -> list
    def get_error_logs(machine_id, window_min) -> list
    def trigger_incident(type, machine=None, custom=None) -> Incident
    def execute_action(incident_id, action) -> ActionResult  # applies effects per taxonomy
    def poll_degradation(incident_id) -> None        # advance time-based dynamics
    def machine_status_all() -> list                  # for dashboard
```

**`execute_action` semantics:** returns `{effect: full_recovery|partial_recovery|no_effect, recovery_pct, re_degraded_after_sec}` per the taxonomy table. Partial effects recover the machine, then a background task re-degrades it after the specified delay.

---

## 6. Hindsight Memory Layer (Phase 2)

### 6.1 The Episode Record (write this on every incident resolution — this is the heart of the project)

Written via `retain()` as structured text (render the JSON to a readable template — Hindsight extracts facts from prose):

```
INCIDENT {incident_id} — {timestamp}
MACHINE: {machine_id} ({machine_profile})

SYMPTOMS: throughput {delta}% over {duration} min, onset {gradual|sudden},
error logs: {log_lines}

CONTEXT: config changed {minutes_before} min before onset; calibration age
{days} days; network status {status}; sensor variance {variance}.

DIAGNOSIS: {type}, confidence {conf}.

ACTIONS ATTEMPTED:
1. {action_1} → {effect_1} ({recovery_pct_1}%{, re-degraded after Xs})
2. {action_2} → {effect_2} ...

RESOLUTION: final action {final_action}, MTTR {mttr} seconds.
OUTCOME: {successful|escalated}.
LESSON: {one-line lesson, e.g. "Restart gives only temporary relief for
config-regression signatures; escalate to rollback directly."}
```

**Also `retain()` partial lessons immediately when a trap fix re-degrades** (don't wait for final resolution — the learning-from-failure beat must persist even if the judge then walks away).

### 6.2 Recall Query Construction

After investigation, build a query from the evidence bundle — **deliberately paraphrased, never keyword-copied** (this proves semantic matching):

```
"machine showing {onset} throughput decline with {key signals from
logs}, config {changed/unchanged} recently, calibration {age}"
```

- Filter returned memories: keep only matches with similarity ≥ **0.7**.
- If Hindsight's recall returns scores, use them; otherwise compute cosine similarity client-side between the query embedding and returned memory text. (Inspect the SDK's return shape at build time and adapt — this is a known unknown.)
- Memory results feed the `decide` node as structured context.

### 6.3 Memory Toggle

The frontend toggle sends `memory_enabled: false` with each incident run. When disabled, the agent **skips the search_memory node entirely** (graph built with a flag, or node returns empty). Everything else identical. This is the live before/after proof.

### 6.4 Bank Config

One bank: `memoryops-incidents`. Mission: *"I am a production incident responder. I learn from every incident resolution to diagnose faster and more accurately."* Set via Hindsight bank config API.

---

## 7. Agent Design (Phase 2)

### 7.1 State

```python
class AgentState(TypedDict):
    incident: dict
    evidence: list[dict]        # collected tool results
    tool_call_count: int
    memory_results: list[dict]  # matches: [{incident_id, similarity, summary}]
    recommendation: dict        # {action, confidence, reasoning, cited_incidents}
    action_taken: dict | None
    outcome: dict | None        # {effect, recovery_pct, re_degraded}
    memory_enabled: bool
    retry_count: int
```

### 7.2 Graph

```
investigate ──▶ search_memory ──▶ decide ──▶ act ──▶ learn
     ▲                                          │
     └──────────── retry (if outcome != full_recovery and
                        retry_count < 2) ───────┘
```

- `investigate`: LLM tool-calling loop. Model chooses among the 4 tools (below), max **10 calls** (guardrail). Every call + result is streamed as SSE. Emits an `investigation_summary` (the evidence bundle).
- `search_memory`: deterministic (not LLM). Builds recall query from evidence, calls Hindsight, filters ≥0.7. Skipped if `memory_enabled=false`.
- `decide`: LLM call. Input: evidence + memory results + incident context. Output MUST validate against Pydantic schema with action whitelist. Reasoning must **cite matched incident IDs** when memory informed the decision. Confidence 0–1.
- `act`: **no LLM**. Suspends awaiting human action (LangGraph interrupt / checkpoint — or simpler: FastAPI holds the recommendation, frontend shows buttons, judge's click resumes the flow). Calls `simulator.execute_action`.
- `learn`: writes episode record + lessons to Hindsight via `retain()`, updates metrics tracker, closes incident in SQLite.
- Retry edge: if `outcome.effect != full_recovery` and retries < 2 → back to `investigate` with prior outcome appended to evidence ("Action X applied, only partial relief").

### 7.3 Tools (exactly 4 — these are the LLM's interface to the world)

```python
get_machine_metrics(machine_id: str) -> dict
get_recent_events(machine_id: str, window_minutes: int) -> list[dict]
get_error_logs(machine_id: str, window_minutes: int) -> list[str]
search_incident_history(query: str) -> list[dict]  # wraps Hindsight recall
```

Descriptions matter — write them so the model understands *when* to use each (e.g., events reveal config deploys/calibrations; logs reveal error patterns).

### 7.4 Prompts (key requirements)

**investigate system prompt:** role = senior production incident responder; use tools to build evidence; do not guess before gathering data; summarize findings.

**decide system prompt:** requirements —
1. Weigh evidence AND memory matches; state which matched incidents you're relying on.
2. If memory matches exist, cite their incident IDs and what fix worked/failed there.
3. If memory results don't fit this incident's signature, say so and ignore them.
4. Recommend ONE primary action + confidence + reasoning.
5. Also list "actions_known_to_fail" for this signature from memory (drives the trap-fix avoidance story).

### 7.5 LLM Client (llm.py)

- Groq chat completions with tool use, `openai/gpt-oss-120b`.
- Retry ×2 with exponential backoff (1s, 2s) per model; on failure of primary → `qwen/qwen3-32b`; on total failure → return `ESCALATE_HUMAN` recommendation (graceful degradation, never crash).
- Catch malformed function-call responses (organizers explicitly warned about this) — on parse failure, retry once, then inject a corrective system message.
- Langfuse callback handler attached to every call.

---

## 8. Guardrails (guardrails.py)

1. **Pydantic validation** on all LLM outputs (`Recommendation` model; action must be in whitelist).
2. **Max 10 tool calls** per investigation → forced conclusion.
3. **Action whitelist** (6 actions, Section 5.2) — unvalidated actions rejected + corrective retry.
4. **LLM retry + model fallback** (7.5).
5. **Simulator state validation**: can't trigger incident on an already-degraded machine; error returned as structured SSE event.
6. **Memory similarity threshold** 0.7 — below it, results are not shown as "matches."

---

## 9. FastAPI + SSE (Phase 3)

**Endpoints:**

```
GET  /api/state                     # all machines + KPIs (dashboard polling)
POST /api/incident/predefined       # {type, machine?} → incident created, agent starts
POST /api/incident/custom           # structured builder payload
POST /api/incident/random            # random type + machine
POST /api/incident/{id}/action       # {action} → resumes agent, executes
POST /api/incident/{id}/ignore       # triggers cascade dynamics
GET  /api/incidents                 # history for memory browser
GET  /api/metrics                   # learning curves data
GET  /api/stream?incident_id=...     # SSE endpoint
```

**SSE event types (contract with frontend):**

```
state_changed        {machines: [...], oee, alerts}
incident_triggered   {incident}
investigation_start  {incident_id}
tool_call            {tool_name, args}
tool_result          {tool_name, result}
memory_search        {query}
memory_results       {matches: [{incident_id, similarity, summary}]}
recommendation       {action, confidence, reasoning, cited_incidents, actions_known_to_fail}
awaiting_action      {options: [...]}
action_executed      {action, effect, recovery_pct}
re_degradation       {incident_id, message}       # trap fix wearing off
outcome              {resolved: bool, mttr_seconds}
memory_written       {episode_summary}
metrics_updated      {series}
error                {code, message}             # guardrail failures, never crash
```

Use `sse-starlette`. Pattern reference: `agent-service-toolkit` repo. The `act` node's human-in-the-loop: after `recommendation` is emitted, the agent run pauses (FastAPI holds state server-side); `POST /api/incident/{id}/action` resumes it. Keep it simple: store the graph state in memory dict keyed by incident ID — no Redis needed.

---

## 10. Frontend (Phase 4)

Next.js App Router + Tailwind + shadcn/ui + Recharts. **Dark theme ops aesthetic.**

### 10.1 Layout

- **Header:** logo "MemoryOps — Self-Learning Production Incident Commander" · **🧠 MEMORY [ON/OFF] toggle** (prominent) · KPI strip (OEE, throughput, active alerts).
- **Main (dashboard):** machine cards grid (M1–M5, status color, throughput, sparkline). Left rail: trigger controls — 4 predefined buttons, **CUSTOM BUILDER** (opens modal), **SURPRISE ME**.
- **Incident view (appears when incident active):** live **Investigation Trace** panel (tool calls/results streaming — this replaces a chat window; it must look like a real-time terminal/trace log), **Memory Match panel** (matched episodes with similarity badges), **Recommendation card** (action, confidence bar, reasoning, cited incidents), **Action buttons** (whitelist + ESCALATE + IGNORE).
- **Memory Browser tab:** list of all episodes from `GET /api/incidents`; click to expand full episode record (the exact text retained to Hindsight).
- **Learning tab (Recharts):** MTTR per incident (bar/line), confidence progression, memory hit rate, first-time-right %. This chart is the money shot — label it "The agent is learning."

### 10.2 Custom Builder Modal

Machine dropdown (M1–M5) · config_changed [Yes/No] · minutes_before slider · throughput delta slider · error rate slider · temperature [Normal/High] · calibration [Fresh/30+ days] · network [Normal/Degraded] · **[RUN CUSTOM INCIDENT]**. No free-text field anywhere.

### 10.3 Behavior

- Dashboard polls `/api/state` every 2s; incident view is pure SSE.
- When memory OFF: memory panel shows "Memory disabled — agent reasoning from evidence only."
- `re_degradation` event animates the machine card flashing down again.

---

## 11. Evaluation Tracker (eval/metrics.py)

SQLite table `metrics(incident_id, type, mttr_seconds, confidence, memory_hit: bool, first_time_right: bool, ts)`.

Compute per incident: MTTR (trigger→resolved), diagnosis confidence, memory hit (any match ≥0.7), first-time-right (first executed action = full recovery). Expose series via `/api/metrics`. **Success trend over ≥5 incidents of a recurring class must show MTTR dropping and confidence rising** — if it doesn't, the demo is broken; this is the acceptance test that matters most.

---

## 12. Demo Script (3.5 min — build everything to serve this)

1. **0:00** Healthy factory. "MemoryOps runs production, and it gets better at its job every day."
2. **0:15** Trigger config regression on M3 → cold investigation trace streams → 68% confidence, generic recommendation → judge applies ROLLBACK → recovery → **memory written — show the episode appear in Memory Browser.**
3. **1:15** Trigger config regression again — **but on M4, different log phrasings** → 94% match, cites INC-001 → resolved in ~40s. "It matched the signature, not the machine."
4. **1:45** Sabotage beat: trigger config regression, apply **RESTART** instead → partial recovery → **re-degrades on stage** → agent self-corrects, escalates to rollback, writes failure lesson to memory.
5. **2:15** Sensor drift → agent does NOT replay "rollback" (different signature) → recommends recalibration. Discrimination proven.
6. **2:40** Learning tab: MTTR curve 22m → 40s.
7. **2:55** Toggle MEMORY OFF, trigger same incident → generic cold behavior. Toggle ON → instant match. Before/after live.
8. **3:15** Close: "Day one it's a rookie. Every incident makes it better. That's what memory is for."

---

## 13. Build Order & Acceptance Criteria

**Phase 0 — Scaffold.** Repo, venv, `.env.example` (HINDSIGHT_API_KEY, HINDSIGHT_BANK_ID, GROQ_API_KEY, LANGFUSE keys), deps pinned. ✅ `pip install -e .` + `uvicorn` boots.

**Phase 1 — Simulator.** ✅ Script triggers each of 4 incident types on random machines; metrics degrade per taxonomy; each whitelist action produces correct effect (incl. re-degradation timers); ignore-after-8-min cascades; custom builder input maps to a class and resolves per its semantics; SQLite persists everything.

**Phase 2 — Agent.** ✅ Headless run (`demo_dryrun.py`): trigger config regression → agent investigates with real tool calls → recommendation validates against whitelist → action executes → episode text retained to Hindsight (verify in Hindsight Cloud UI) → trigger reworded same-type incident on different machine → recall returns prior episode ≥0.7 → confidence rises, reasoning cites INC-ID → trap-fix retry loop works (restart → re-degrade → re-investigate → rollback).

**Phase 3 — API + SSE.** ✅ All endpoints work; SSE streams the full event contract for a live incident; human-in-the-loop resume via action endpoint works; guardrails emit `error` events instead of crashing.

**Phase 4 — Frontend.** ✅ Full demo script (Section 12) runs end-to-end in the browser, including memory toggle behavior and live re-degradation animation.

**Phase 5 — Polish.** ✅ Learning charts correct; Memory Browser shows real Hindsight content; Langfuse traces present; seed script for optional "6-month history" load; README with architecture diagram, how to run, and "How Hindsight memory is used" section (required submission deliverable).

**Known unknowns to resolve early (spike in Phase 2 first hour):** exact Hindsight Python SDK method signatures and recall score format; LangGraph interrupt pattern vs. server-side pause for the act node — pick whichever is less code.

---

*End of specification. When something is ambiguous, choose the option that makes memory (retain → recall → visibly better outcomes) most prominent in the UI and the demo.*