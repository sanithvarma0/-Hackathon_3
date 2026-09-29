// One pure reducer turns the SSE event stream into everything the screen shows.
// The same reducer replays an incident's stored events after a page reload, so a
// reloaded page and a page that watched live end up in exactly the same state.

import type {
  Action,
  BusEvent,
  IncidentView,
  MemoryMatch,
  MetricsRow,
  PlantState,
  Recommendation,
  StateResponse,
} from "@/lib/types";

export const SPARK_POINTS = 60;
export const MAX_TRACE = 400;
export const MAX_TOASTS = 4;

export type Connection = "connecting" | "live" | "reconnecting" | "offline";

export type TraceKind = "attempt" | "tool" | "memory" | "guardrail" | "system" | "error" | "agent";

export interface TraceLine {
  id: number;
  ts: number;
  kind: TraceKind;
  text: string;
  detail?: string; // collapsible body (tool result, retained text)
  ok?: boolean;
  muted?: boolean; // memory touchpoint skipped (memory OFF): grey, not purple
}

export type Phase =
  | "detecting" // triggered, alert not fired yet
  | "investigating"
  | "recommending" // recommendation in, graph not paused yet
  | "awaiting"
  | "verifying"
  | "outcome";

export interface VerifyState {
  action: string;
  startedTs: number; // real seconds (event ts)
  windowSimS: number;
  effect?: string;
  peakPct?: number;
}

export interface IncidentRun {
  id: string;
  view: IncidentView | null;
  memoryEnabled: boolean;
  phase: Phase;
  attempt: number;
  trace: TraceLine[];
  toolCalls: number;
  focusMachine: string | null;
  hints: string[];
  hintsQuery: string | null;
  matches: MemoryMatch[];
  searched: boolean; // search_memory ran (or was skipped) this attempt
  memoryGate: string | null; // "reranker" normally; "llm_judge" / "rank_only" when degraded
  learnedPatterns: string[];
  memorySkipped: boolean;
  recommendation: Recommendation | null;
  options: Action[];
  recommended: Action | null;
  failedActions: string[]; // tried this incident and wore off / did nothing
  verify: VerifyState | null;
  outcome: { resolved: boolean; escalated: boolean; mttr_sim_s: number | null } | null;
  lessons: string[];
  redegradedTs: number | null;
  metrics: MetricsRow | null;
  traceUrl: string | null;
}

export type ToastTone = "memory" | "critical" | "degraded" | "info" | "healthy";

export interface Toast {
  id: number;
  tone: ToastTone;
  title: string;
  body?: string;
}

export interface Point {
  ts: number;
  v: number;
}

export interface UIState {
  connection: Connection;
  plant: PlantState | null;
  simSpeed: number | null;
  bankId: string | null;
  lastEventId: number;
  spark: Record<string, Point[]>; // throughput per machine, sim time
  configTicks: Record<string, number[]>; // sim ts where config_version changed
  incident: IncidentRun | null;
  toasts: Toast[];
  runbook: { content: string; ts: number } | null;
  // Bumped when server-side lists changed, so tabs know to refetch.
  memoryVersion: number;
  metricsVersion: number;
  usageVersion: number;
  localSeq: number;
}

export type UIAction =
  | { type: "snapshot"; state: StateResponse }
  | { type: "history"; data: Record<string, { ts: number; value: number }[]> }
  | { type: "event"; event: BusEvent }
  | { type: "replay"; events: BusEvent[] } // stored history after a reload: no toasts
  | { type: "resync" } // (re)connecting: forget the event cursor, the replay rebuilds the run
  | { type: "connection"; status: Connection }
  | { type: "dismiss_toast"; id: number }
  | { type: "toast"; toast: Omit<Toast, "id"> }; // client-side (e.g. backend unreachable)

export const initialState: UIState = {
  connection: "connecting",
  plant: null,
  simSpeed: null,
  bankId: null,
  lastEventId: 0,
  spark: {},
  configTicks: {},
  incident: null,
  toasts: [],
  runbook: null,
  memoryVersion: 0,
  metricsVersion: 0,
  usageVersion: 0,
  localSeq: 0,
};

export function newRun(id: string, view: IncidentView | null): IncidentRun {
  return {
    id,
    view,
    memoryEnabled: view?.memory_enabled ?? true,
    phase: "detecting",
    attempt: 0,
    trace: [],
    toolCalls: 0,
    focusMachine: null,
    hints: [],
    hintsQuery: null,
    matches: [],
    searched: false,
    memoryGate: null,
    learnedPatterns: [],
    memorySkipped: false,
    recommendation: null,
    options: [],
    recommended: null,
    failedActions: [],
    verify: null,
    outcome: null,
    lessons: [],
    redegradedTs: null,
    metrics: null,
    traceUrl: null,
  };
}

// ---- helpers -----------------------------------------------------------------------------

const str = (v: unknown): string => (typeof v === "string" ? v : v == null ? "" : String(v));
const num = (v: unknown): number | null => (typeof v === "number" ? v : null);

export function parseArgs(raw: unknown): Record<string, unknown> {
  if (raw && typeof raw === "object") return raw as Record<string, unknown>;
  try {
    const parsed = JSON.parse(str(raw));
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

/** `get_metric_history(M3, throughput_pct, 24h)` — compact, readable on a projector. */
export function formatToolCall(tool: string, args: Record<string, unknown>): string {
  const parts: string[] = [];
  if (args.machine_id) parts.push(str(args.machine_id));
  if (args.metric) parts.push(str(args.metric));
  if (args.window_hours != null) parts.push(`${args.window_hours}h`);
  if (args.window_minutes != null) parts.push(`${args.window_minutes}m`);
  for (const [k, v] of Object.entries(args)) {
    if (!["machine_id", "metric", "window_hours", "window_minutes"].includes(k)) {
      parts.push(`${k}=${str(v)}`);
    }
  }
  return `${tool}(${parts.join(", ")})`;
}

function pushTrace(run: IncidentRun, line: Omit<TraceLine, "id">, id: number): IncidentRun {
  const trace = [...run.trace, { ...line, id }];
  return { ...run, trace: trace.length > MAX_TRACE ? trace.slice(-MAX_TRACE) : trace };
}

function pushToast(state: UIState, toast: Omit<Toast, "id">, id: number): UIState {
  const toasts = [...state.toasts, { ...toast, id }].slice(-MAX_TOASTS);
  return { ...state, toasts };
}

function appendSpark(
  spark: Record<string, Point[]>,
  plant: PlantState,
): Record<string, Point[]> {
  const next: Record<string, Point[]> = { ...spark };
  for (const m of plant.machines) {
    const series = next[m.machine_id] ?? [];
    const last = series[series.length - 1];
    if (last && last.ts >= plant.ts) continue; // same sim step (or older): nothing new
    next[m.machine_id] = [...series, { ts: plant.ts, v: m.throughput_pct }].slice(-SPARK_POINTS);
  }
  return next;
}

function trackConfig(
  ticks: Record<string, number[]>,
  prev: PlantState | null,
  plant: PlantState,
): Record<string, number[]> {
  if (!prev) return ticks;
  let next = ticks;
  for (const m of plant.machines) {
    const before = prev.machines.find((p) => p.machine_id === m.machine_id);
    if (before && before.config_version !== m.config_version) {
      next = { ...next, [m.machine_id]: [...(next[m.machine_id] ?? []), plant.ts].slice(-5) };
    }
  }
  return next;
}

// ---- the reducer ---------------------------------------------------------------------------

export function reducer(state: UIState, action: UIAction): UIState {
  switch (action.type) {
    case "snapshot": {
      const { plant } = action.state;
      const active = action.state.active_incident;
      let incident = state.incident;
      if (active && incident?.id !== active.id) incident = newRun(active.id, active);
      else if (active && incident) incident = { ...incident, view: active };
      return {
        ...state,
        plant,
        simSpeed: action.state.sim_speed,
        bankId: action.state.bank_id,
        lastEventId: action.state.last_event_id, // the server's cursor is authoritative
        spark: appendSpark(state.spark, plant),
        incident,
      };
    }
    case "history": {
      const spark: Record<string, Point[]> = { ...state.spark };
      for (const [mid, points] of Object.entries(action.data)) {
        const older = points.map((p) => ({ ts: p.ts, v: p.value }));
        const newer = (spark[mid] ?? []).filter((p) => !older.length || p.ts > older[older.length - 1].ts);
        spark[mid] = [...older, ...newer].slice(-SPARK_POINTS);
      }
      return { ...state, spark };
    }
    case "connection":
      return { ...state, connection: action.status };
    case "toast":
      // negative IDs never collide with event IDs
      return pushToast({ ...state, localSeq: state.localSeq + 1 }, action.toast, -1 - state.localSeq);
    case "dismiss_toast":
      return { ...state, toasts: state.toasts.filter((t) => t.id !== action.id) };
    case "event":
      return applyEvent(state, action.event);
    case "resync":
      return { ...state, lastEventId: 0, incident: null };
    case "replay": {
      const replayed = action.events.reduce(applyEvent, state);
      return { ...replayed, toasts: state.toasts };
    }
  }
}

export function applyEvent(state: UIState, e: BusEvent): UIState {
  if (e.id <= state.lastEventId && e.type !== "state_changed") return state; // replay overlap
  const s: UIState = { ...state, lastEventId: Math.max(state.lastEventId, e.id) };
  const d = e.data;

  // Plant-wide events first.
  switch (e.type) {
    case "state_changed": {
      const plant = d as unknown as PlantState;
      return {
        ...s,
        plant,
        spark: appendSpark(s.spark, plant),
        configTicks: trackConfig(s.configTicks, s.plant, plant),
      };
    }
    case "runbook_updated":
      return pushToast(
        { ...s, runbook: { content: str(d.content), ts: e.ts }, memoryVersion: s.memoryVersion + 1 },
        { tone: "memory", title: "Runbook updated", body: "Hindsight rewrote the Incident Patterns runbook" },
        e.id,
      );
    case "reset":
      return pushToast(
        { ...s, incident: null, runbook: null, memoryVersion: s.memoryVersion + 1, metricsVersion: s.metricsVersion + 1, bankId: str(d.bank_id) || s.bankId },
        { tone: "info", title: d.wipe_memory ? "Memory wiped — fresh start" : "Plant reset", body: `Bank ${str(d.bank_id)}` },
        e.id,
      );
    case "error":
      if (!e.incident_id || e.incident_id !== s.incident?.id) {
        return pushToast(s, { tone: "critical", title: humanCode(str(d.code)), body: str(d.message) }, e.id);
      }
  }

  if (!e.incident_id) return s;

  // Incident events. A new incident ID starts a fresh run.
  let run = s.incident;
  if (e.type === "incident_triggered") {
    const view = (d.incident ?? null) as IncidentView | null;
    run = newRun(e.incident_id, view);
    run = pushTrace(run, { ts: e.ts, kind: "system", text: `${e.incident_id} triggered on ${view?.machine_id ?? "?"} · waiting for the alert` }, e.id);
    return { ...s, incident: run };
  }
  if (!run || run.id !== e.incident_id) run = newRun(e.incident_id, null);

  let out: UIState = s;
  switch (e.type) {
    case "incident_detected":
      run = pushTrace({ ...run, phase: "investigating" }, { ts: e.ts, kind: "error", text: `ALERT · ${str(d.message)}` }, e.id);
      break;
    case "investigation_queued":
      run = { ...run, memoryEnabled: d.memory_enabled !== false, phase: "investigating" };
      break;
    case "memory_hints": {
      const hints = Array.isArray(d.hints) ? d.hints.map(str) : [];
      run = pushTrace(
        { ...run, hints, hintsQuery: str(d.query) },
        { ts: e.ts, kind: "memory", text: `recall_hints · ${hints.length ? `${hints.length} hint${hints.length > 1 ? "s" : ""} from past incidents` : "no similar past incidents"}`, detail: hints.length ? hints.map((h) => `• ${h}`).join("\n") : str(d.query) },
        e.id,
      );
      break;
    }
    case "memory_skipped":
      run = pushTrace(
        { ...run, memorySkipped: d.node === "recall_hints" || run.memorySkipped, searched: run.searched || d.node === "search_memory" },
        { ts: e.ts, kind: "memory", muted: true, text: `${str(d.node)} skipped · ${str(d.reason) || "memory disabled"}` },
        e.id,
      );
      break;
    case "investigation_start": {
      const attempt = num(d.attempt) ?? run.attempt + 1;
      run = pushTrace({ ...run, attempt, phase: "investigating", verify: attempt > 1 ? run.verify : null }, { ts: e.ts, kind: "attempt", text: `Attempt ${attempt}` }, e.id);
      break;
    }
    case "tool_call": {
      const args = parseArgs(d.args);
      const machine = typeof args.machine_id === "string" ? args.machine_id : null;
      // The agent asking memory mid-investigation is memory at work: purple, not a plain tool.
      const recall = d.tool_name === "recall_similar_incidents";
      run = pushTrace(
        { ...run, toolCalls: run.toolCalls + 1, focusMachine: machine ?? run.focusMachine },
        recall
          ? { ts: e.ts, kind: "memory", text: "recall_similar_incidents · asking memory mid-investigation" }
          : { ts: e.ts, kind: "tool", text: formatToolCall(str(d.tool_name), args) },
        e.id,
      );
      break;
    }
    case "tool_result": {
      // Attach to the matching call line as its collapsible body.
      const trace = [...run.trace];
      for (let i = trace.length - 1; i >= 0; i--) {
        const isCall = trace[i].kind === "tool" || trace[i].text.startsWith("recall_similar_incidents");
        if (isCall && trace[i].detail === undefined) {
          trace[i] = { ...trace[i], detail: str(d.result), ok: d.ok !== false };
          break;
        }
      }
      run = { ...run, trace };
      break;
    }
    case "guardrail":
      run = pushTrace(run, { ts: e.ts, kind: "guardrail", text: `guardrail · ${str(d.message)}` }, e.id);
      break;
    case "investigation_summary": {
      const summary = (d.summary ?? {}) as Record<string, unknown>;
      run = pushTrace(
        { ...run, focusMachine: null },
        { ts: e.ts, kind: "agent", text: `summary · onset ${str(summary.onset) || "?"}, ${num(d.tool_call_count) ?? run.toolCalls} tool calls`, detail: str(summary.summary) },
        e.id,
      );
      break;
    }
    case "memory_search":
      run = pushTrace(run, { ts: e.ts, kind: "memory", text: "search_memory · looking for incidents with this signature", detail: str(d.query) }, e.id);
      break;
    case "memory_results": {
      const matches = (Array.isArray(d.matches) ? d.matches : []) as MemoryMatch[];
      const learned = Array.isArray(d.learned_patterns) ? d.learned_patterns.map(str) : [];
      run = pushTrace(
        { ...run, matches, learnedPatterns: learned, searched: true, memoryGate: typeof d.gate === "string" ? d.gate : run.memoryGate },
        { ts: e.ts, kind: "memory", text: matches.length ? `matched ${matches.map((m) => `${m.incident_id} (${m.strength})`).join(", ")}` : "no matching past incident" },
        e.id,
      );
      break;
    }
    case "recommendation": {
      const rec = d as unknown as Recommendation;
      run = pushTrace(
        { ...run, recommendation: rec, phase: "recommending" },
        { ts: e.ts, kind: "agent", text: `recommend ${rec.action} · ${Math.round((rec.calibrated_confidence ?? rec.confidence) * 100)}%`, detail: rec.reasoning },
        e.id,
      );
      break;
    }
    case "awaiting_action":
      out = { ...out, usageVersion: out.usageVersion + 1 }; // LLM spend just happened
      run = {
        ...run,
        phase: "awaiting",
        options: (Array.isArray(d.options) ? d.options : []) as Action[],
        recommended: (d.recommended ?? run.recommendation?.action ?? null) as Action | null,
      };
      break;
    case "incident_ignored":
      run = pushTrace(run, { ts: e.ts, kind: "guardrail", text: "IGNORED · the condition worsens; still waiting for a decision" }, e.id);
      out = pushToast(out, { tone: "degraded", title: "Ignored", body: "The condition got worse. The incident is still open." }, e.id);
      break;
    case "action_executed":
      run = pushTrace(
        { ...run, phase: "verifying", verify: { action: str(d.action), startedTs: e.ts, windowSimS: run.verify?.windowSimS ?? 180 } },
        { ts: e.ts, kind: "system", text: `executed ${str(d.action)}${d.recommended && d.recommended !== d.action ? ` (override of ${str(d.recommended)})` : ""}`, detail: str(d.message) },
        e.id,
      );
      break;
    case "verifying":
      run = { ...run, phase: "verifying", verify: { action: run.verify?.action ?? "", startedTs: e.ts, windowSimS: num(d.window_sim_s) ?? 180 } };
      break;
    case "verify_result": {
      const effect = str(d.effect);
      const failed = effect !== "full_recovery" && effect !== "escalated";
      run = pushTrace(
        {
          ...run,
          verify: run.verify ? { ...run.verify, effect, peakPct: num(d.peak_throughput_pct) ?? undefined } : null,
          failedActions: failed && !run.failedActions.includes(str(d.action)) ? [...run.failedActions, str(d.action)] : run.failedActions,
        },
        { ts: e.ts, kind: effect === "full_recovery" ? "agent" : "guardrail", text: `verify · ${effect.replace(/_/g, " ")} (peak ${num(d.peak_throughput_pct) ?? "?"}%)` },
        e.id,
      );
      break;
    }
    case "re_degradation":
      run = pushTrace({ ...run, redegradedTs: e.ts }, { ts: e.ts, kind: "error", text: `RE-DEGRADED · ${str(d.message)}` }, e.id);
      out = pushToast(out, { tone: "critical", title: `${run.verify?.action || "The fix"} gave only temporary relief`, body: str(d.message) }, e.id);
      break;
    case "lesson_written":
      run = pushTrace({ ...run, lessons: [...run.lessons, str(d.text)] }, { ts: e.ts, kind: "memory", text: "retain · lesson written to memory", detail: str(d.text) }, e.id);
      out = pushToast({ ...out, memoryVersion: out.memoryVersion + 1 }, { tone: "memory", title: "Lesson written to memory", body: firstLine(str(d.text)) }, e.id);
      break;
    case "outcome":
      run = pushTrace(
        { ...run, phase: "outcome", outcome: { resolved: d.resolved === true, escalated: d.escalated === true, mttr_sim_s: num(d.mttr_sim_s) } },
        { ts: e.ts, kind: d.resolved ? "agent" : "guardrail", text: d.resolved ? `RESOLVED · MTTR ${fmtDuration(num(d.mttr_sim_s))} (sim)` : "ESCALATED to the on-call engineer" },
        e.id,
      );
      break;
    case "memory_written":
      run = pushTrace(run, { ts: e.ts, kind: "memory", text: `retain · episode ${str(d.document_id)} written to memory`, detail: str(d.text) }, e.id);
      out = pushToast({ ...out, memoryVersion: out.memoryVersion + 1 }, { tone: "memory", title: `${str(d.document_id)} retained to memory`, body: "The next similar incident can recall it" }, e.id);
      break;
    case "memory_synced":
      out = { ...out, memoryVersion: out.memoryVersion + 1 };
      break;
    case "incident_closed":
      run = { ...run, view: run.view ? { ...run.view, status: str(d.message) === "resolved" ? "resolved" : "escalated" } : run.view };
      break;
    case "metrics_updated":
      run = {
        ...run,
        metrics: (d.row ?? null) as MetricsRow | null,
        view: (d.incident ?? run.view) as IncidentView | null,
        traceUrl: typeof d.trace === "string" ? d.trace : run.traceUrl,
      };
      out = { ...out, metricsVersion: out.metricsVersion + 1, usageVersion: out.usageVersion + 1 };
      break;
    case "error":
      run = pushTrace(run, { ts: e.ts, kind: "error", text: `${humanCode(str(d.code))} · ${str(d.message)}` }, e.id);
      out = pushToast(out, { tone: "critical", title: humanCode(str(d.code)), body: str(d.message) }, e.id);
      break;
  }
  return { ...out, incident: run };
}

// ---- formatting shared by components ----------------------------------------------------------

export function fmtDuration(s: number | null | undefined): string {
  if (s == null) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return r ? `${m}m ${r}s` : `${m}m`;
}

export function humanCode(code: string): string {
  const known: Record<string, string> = {
    MEMORY_UNAVAILABLE: "Memory unavailable",
    LLM_UNAVAILABLE: "Language model unavailable",
    INCIDENT_ACTIVE: "An incident is already active",
    NOT_AWAITING_ACTION: "Not waiting for a decision",
    BACKEND_UNREACHABLE: "Backend unreachable",
  };
  return known[code] ?? code.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}

function firstLine(text: string): string {
  const line = text.split("\n").find((l) => l.trim()) ?? "";
  return line.length > 140 ? `${line.slice(0, 137)}…` : line;
}
