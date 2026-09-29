import { describe, expect, it } from "vitest";
import { formatToolCall, initialState, reducer, type UIState } from "@/lib/reducer";
import type { BusEvent, MachineMetrics, PlantState } from "@/lib/types";

// Payload shapes copied from a recorded live /api/stream run (BUILD_PLAN.md 14.1d).

let nextId = 1;
const ev = (type: string, data: Record<string, unknown> = {}, incident: string | null = "INC-001"): BusEvent => ({
  id: nextId++,
  ts: 1_000 + nextId,
  type,
  incident_id: incident,
  data,
});

const machine = (id: string, over: Partial<MachineMetrics> = {}): MachineMetrics => ({
  machine_id: id,
  name: id,
  profile: "",
  gateway: "GW-A",
  status: "healthy",
  throughput_pct: 96,
  oee_pct: 90,
  error_rate_pct: 0.5,
  temperature_c: 40,
  sensor_variance: 0.03,
  packet_loss_pct: 0.2,
  latency_ms: 3,
  memory_pct: 45,
  config_version: "v1.0.0",
  calibration_age_days: 10,
  ts: 0,
  ...over,
});

const plant = (ts: number, m3: Partial<MachineMetrics> = {}): PlantState => ({
  ts,
  machines: [machine("M1"), machine("M3", m3)],
  starved: [],
  gateways: [],
  line_throughput_pct: 95,
  oee_pct: 90,
  alerts: 0,
  active_incident_id: null,
});

const view = { id: "INC-001", machine_id: "M3", memory_enabled: true, type: null, status: "open" };

function configRegression(): BusEvent[] {
  return [
    ev("incident_triggered", { incident: view }),
    ev("incident_detected", { message: "throughput below alert threshold" }),
    ev("investigation_queued", { memory_enabled: true }),
    ev("memory_hints", { query: "q", hints: ["config deploy preceded the drop"], matches: 1 }),
    ev("investigation_start", { attempt: 1 }),
    ev("tool_call", { step: 1, tool_name: "get_machine_metrics", args: '{"machine_id":"M3"}' }),
    ev("tool_result", { step: 1, tool_name: "get_machine_metrics", ok: true, result: "M3 degraded" }),
    ev("investigation_summary", { summary: { onset: "sudden", summary: "localized" }, tool_call_count: 1 }),
    ev("memory_search", { query: "abrupt output decline" }),
    ev("memory_results", {
      matches: [{ incident_id: "INC-000", rank: 1, rerank: 0.8, similarity: 0.6, strength: "strong", diagnosis: "config", final_action: "ROLLBACK_CONFIG", outcome: "resolved", facts: [] }],
      learned_patterns: ["deploys precede regressions"],
    }),
    ev("recommendation", { action: "ROLLBACK_CONFIG", diagnosis: "config regression", confidence: 0.97, calibrated_confidence: 0.95, reasoning: "r", cited_incidents: ["INC-000"], actions_known_to_fail: [] }),
    ev("awaiting_action", { recommended: "ROLLBACK_CONFIG", options: ["ROLLBACK_CONFIG", "RESTART_MACHINE", "ESCALATE_HUMAN"] }),
  ];
}

const live = (s: UIState, events: BusEvent[]) =>
  events.reduce((acc, event) => reducer(acc, { type: "event", event }), s);

describe("incident lifecycle", () => {
  it("walks detect → investigate → recommend → await", () => {
    const s = live(initialState, configRegression());
    const run = s.incident!;
    expect(run.phase).toBe("awaiting");
    expect(run.hints).toEqual(["config deploy preceded the drop"]);
    expect(run.matches[0].incident_id).toBe("INC-000");
    expect(run.learnedPatterns).toEqual(["deploys precede regressions"]);
    expect(run.recommended).toBe("ROLLBACK_CONFIG");
    expect(run.options).toContain("ESCALATE_HUMAN");
    expect(run.toolCalls).toBe(1);
    const tool = run.trace.find((l) => l.kind === "tool")!;
    expect(tool.text).toBe("get_machine_metrics(M3)");
    expect(tool.detail).toBe("M3 degraded"); // result folded into its call line
    expect(run.trace.filter((l) => l.kind === "memory").length).toBe(3);
  });

  it("focuses the machine the agent is looking at, and releases it after the summary", () => {
    const events = configRegression();
    const upToCall = live(initialState, events.slice(0, 6));
    expect(upToCall.incident!.focusMachine).toBe("M3");
    const afterSummary = live(upToCall, events.slice(6, 8));
    expect(afterSummary.incident!.focusMachine).toBeNull();
  });

  it("verifies, resolves, retains — with purple toasts only for memory writes", () => {
    const s = live(initialState, [
      ...configRegression(),
      ev("action_executed", { action: "ROLLBACK_CONFIG", recommended: "ROLLBACK_CONFIG", message: "rolled back" }),
      ev("verifying", { window_sim_s: 180 }),
      ev("verify_result", { action: "ROLLBACK_CONFIG", effect: "full_recovery", peak_throughput_pct: 97 }),
      ev("incident_closed", { message: "resolved" }),
      ev("outcome", { resolved: true, escalated: false, mttr_sim_s: 568 }),
      ev("memory_written", { document_id: "INC-001", text: "INCIDENT INC-001" }),
      ev("metrics_updated", { row: { incident_id: "INC-001", recommendation_correct: 1 }, incident: { ...view, type: "config_regression", status: "resolved" } }),
    ]);
    const run = s.incident!;
    expect(run.phase).toBe("outcome");
    expect(run.outcome).toEqual({ resolved: true, escalated: false, mttr_sim_s: 568 });
    expect(run.verify?.windowSimS).toBe(180);
    expect(run.verify?.effect).toBe("full_recovery");
    expect(run.view?.type).toBe("config_regression"); // revealed only once closed
    expect(s.toasts.map((t) => t.tone)).toEqual(["memory"]);
    expect(s.memoryVersion).toBe(1);
    expect(s.metricsVersion).toBe(1);
  });

  it("a trap fix that wears off records the failed action, a lesson and a second attempt", () => {
    const s = live(initialState, [
      ...configRegression(),
      ev("action_executed", { action: "RESTART_MACHINE", recommended: "ROLLBACK_CONFIG" }),
      ev("verifying", { window_sim_s: 180 }),
      ev("re_degradation", { message: "throughput fell again 90 s after the restart" }),
      ev("verify_result", { action: "RESTART_MACHINE", effect: "partial_recovery", peak_throughput_pct: 92 }),
      ev("lesson_written", { document_id: "INC-001:lesson-1", text: "LESSON: RESTART_MACHINE only helped briefly" }),
      ev("investigation_start", { attempt: 2 }),
    ]);
    const run = s.incident!;
    expect(run.attempt).toBe(2);
    expect(run.phase).toBe("investigating");
    expect(run.failedActions).toEqual(["RESTART_MACHINE"]);
    expect(run.redegradedTs).not.toBeNull();
    expect(run.lessons).toHaveLength(1);
    expect(run.trace.find((l) => l.text.startsWith("executed"))!.text).toContain("override of ROLLBACK_CONFIG");
    expect(s.toasts.map((t) => t.tone)).toEqual(["critical", "memory"]);
  });

  it("a new incident starts a clean run", () => {
    const s = live(initialState, [...configRegression(), ev("incident_triggered", { incident: { ...view, id: "INC-002" } }, "INC-002")]);
    expect(s.incident!.id).toBe("INC-002");
    expect(s.incident!.matches).toEqual([]);
    expect(s.incident!.phase).toBe("detecting");
  });

  it("memory OFF is visible as skipped touchpoints", () => {
    const s = live(initialState, [
      ev("incident_triggered", { incident: { ...view, memory_enabled: false } }),
      ev("investigation_queued", { memory_enabled: false }),
      ev("memory_skipped", { node: "recall_hints" }),
    ]);
    expect(s.incident!.memoryEnabled).toBe(false);
    expect(s.incident!.memorySkipped).toBe(true);
    expect(s.incident!.trace.at(-1)).toMatchObject({ kind: "memory", muted: true }); // grey, not purple
  });
});

describe("memory mid-investigation", () => {
  it("shows the agent asking memory as a purple line with the answer folded in", () => {
    const s = live(initialState, [
      ev("incident_triggered", { incident: view }),
      ev("tool_call", { step: 1, tool_name: "recall_similar_incidents", args: '{"observations":"output loss after deploy"}' }),
      ev("tool_result", { step: 1, tool_name: "recall_similar_incidents", ok: true, result: "INC-000 (strong match)" }),
    ]);
    const line = s.incident!.trace.at(-1)!;
    expect(line.kind).toBe("memory");
    expect(line.detail).toBe("INC-000 (strong match)");
    expect(s.incident!.toolCalls).toBe(1);
  });
});

describe("degraded memory", () => {
  it("records how matches were gated so the panel can say so", () => {
    const s = live(initialState, [
      ev("incident_triggered", { incident: view }),
      ev("memory_results", { matches: [], learned_patterns: [], gate: "llm_judge" }),
    ]);
    expect(s.incident!.memoryGate).toBe("llm_judge");
  });
});

describe("replay and reconnects", () => {
  it("replaying stored events restores the same state without toasts", () => {
    const events = [...configRegression(), ev("memory_written", { document_id: "INC-001", text: "t" })];
    const watched = live(initialState, events);
    const reloaded = reducer(initialState, { type: "replay", events });
    expect(reloaded.incident).toEqual(watched.incident);
    expect(watched.toasts).toHaveLength(1);
    expect(reloaded.toasts).toHaveLength(0);
  });

  it("drops events it has already applied (stream overlap after a replay)", () => {
    const events = configRegression();
    const once = reducer(initialState, { type: "replay", events });
    const twice = live(once, events);
    expect(twice.incident!.trace).toEqual(once.incident!.trace);
    expect(twice.incident!.toolCalls).toBe(1);
  });

  it("a resync after a disconnect rebuilds the run from the replay", () => {
    const events = configRegression();
    const before = reducer(initialState, { type: "replay", events });
    let s = reducer(before, { type: "resync" });
    expect(s.incident).toBeNull();
    s = reducer(s, { type: "replay", events });
    expect(s.incident).toEqual(before.incident);
  });

  it("snapshot takes the server's cursor, even if the server's IDs restarted lower", () => {
    const high = { ...initialState, lastEventId: 900 };
    const s = reducer(high, {
      type: "snapshot",
      state: { plant: plant(100), active_incident: null, bank_id: "live", sim_speed: 10, last_event_id: 12 },
    });
    expect(s.lastEventId).toBe(12);
  });

  it("snapshot moves lastEventId forward so the stream opens at the right place", () => {
    const s = reducer(initialState, {
      type: "snapshot",
      state: { plant: plant(100), active_incident: null, bank_id: "live", sim_speed: 10, last_event_id: 42 },
    });
    expect(s.lastEventId).toBe(42);
    expect(s.spark.M3).toEqual([{ ts: 100, v: 96 }]);
  });
});

describe("plant signals", () => {
  it("sparklines append one point per sim step and merge the backfilled history", () => {
    let s = reducer(initialState, { type: "event", event: ev("state_changed", plant(100) as unknown as Record<string, unknown>, null) });
    s = reducer(s, { type: "event", event: ev("state_changed", plant(100) as unknown as Record<string, unknown>, null) });
    s = reducer(s, { type: "event", event: ev("state_changed", plant(110, { throughput_pct: 80 }) as unknown as Record<string, unknown>, null) });
    expect(s.spark.M3.map((p) => p.v)).toEqual([96, 80]);
    s = reducer(s, { type: "history", data: { M3: [{ ts: 80, value: 95 }, { ts: 90, value: 94 }, { ts: 100, value: 96 }] } });
    expect(s.spark.M3.map((p) => p.ts)).toEqual([80, 90, 100, 110]);
  });

  it("marks a config change on the machine's sparkline", () => {
    let s = reducer(initialState, { type: "event", event: ev("state_changed", plant(100) as unknown as Record<string, unknown>, null) });
    s = reducer(s, { type: "event", event: ev("state_changed", plant(110, { config_version: "v1.1.0" }) as unknown as Record<string, unknown>, null) });
    expect(s.configTicks.M3).toEqual([110]);
    expect(s.configTicks.M1).toBeUndefined();
  });

  it("plant-wide errors become calm toasts, never crashes", () => {
    const s = live(initialState, [ev("error", { code: "INCIDENT_ACTIVE", message: "INC-001 is open" }, null)]);
    expect(s.toasts[0]).toMatchObject({ tone: "critical", title: "An incident is already active" });
  });
});

describe("formatting", () => {
  it("renders tool calls compactly", () => {
    expect(formatToolCall("get_metric_history", { machine_id: "M3", metric: "throughput_pct", window_hours: 24 })).toBe(
      "get_metric_history(M3, throughput_pct, 24h)",
    );
    expect(formatToolCall("get_error_logs", { machine_id: "M1", window_minutes: 240 })).toBe("get_error_logs(M1, 240m)");
  });
});
