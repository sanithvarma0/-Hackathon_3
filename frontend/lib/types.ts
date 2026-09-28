// Mirrors backend/schemas.py, backend/service.py and the SSE contract (BUILD_PLAN.md 9).

export const ACTIONS = [
  "ROLLBACK_CONFIG",
  "RESTART_MACHINE",
  "RECALIBRATE_SENSOR",
  "RESTART_GATEWAY",
  "CLEAR_CACHE",
  "ESCALATE_HUMAN",
] as const;
export type Action = (typeof ACTIONS)[number];

export const PREDEFINED = [
  { type: "config_regression", label: "Config regression" },
  { type: "sensor_drift", label: "Sensor drift" },
  { type: "network_failure", label: "Network failure" },
  { type: "resource_exhaustion", label: "Resource exhaustion" },
] as const;
export type IncidentType = (typeof PREDEFINED)[number]["type"] | "ambiguous";

export type MachineStatus = "healthy" | "degraded" | "critical" | "recovering";

export interface MachineMetrics {
  machine_id: string;
  name: string;
  profile: string;
  gateway: string;
  status: MachineStatus;
  throughput_pct: number;
  oee_pct: number;
  error_rate_pct: number;
  temperature_c: number;
  sensor_variance: number;
  packet_loss_pct: number;
  latency_ms: number;
  memory_pct: number;
  config_version: string;
  calibration_age_days: number;
  ts: number;
}

export interface GatewayState {
  gateway_id: string;
  machines: string[];
  status: MachineStatus;
  packet_loss_pct: number;
}

export interface PlantState {
  ts: number; // sim seconds
  machines: MachineMetrics[];
  starved: string[];
  gateways: GatewayState[];
  line_throughput_pct: number;
  oee_pct: number;
  alerts: number;
  active_incident_id: string | null;
}

export type AgentStatus =
  | "waiting_detection"
  | "investigating"
  | "awaiting_action"
  | "finished"
  | "failed";

export interface IncidentView {
  id: string;
  machine_id: string;
  affected: string[];
  gateway: string | null;
  status: "open" | "awaiting_action" | "verifying" | "resolved" | "escalated";
  onset_ts: number;
  detected_ts: number | null;
  resolved_ts: number | null;
  mttr_sim_s: number | null;
  human_wait_sim_s: number;
  resolution_action: string | null;
  engineer_note: string | null;
  type: IncidentType | null; // null until closed, unless the judge picked it
  source: "predefined" | "custom" | "random";
  memory_enabled: boolean;
  agent_status: AgentStatus;
}

export interface StateResponse {
  plant: PlantState;
  active_incident: IncidentView | null;
  bank_id: string;
  sim_speed: number | null;
  last_event_id: number;
}

export interface BusEvent {
  id: number;
  ts: number; // real seconds
  type: string;
  incident_id: string | null;
  data: Record<string, unknown>;
}

export interface MemoryMatch {
  incident_id: string;
  rank: number;
  rerank: number;
  similarity: number | null;
  strength: "strong" | "weak";
  diagnosis: string | null;
  final_action: string | null;
  outcome: string | null;
  facts: string[];
}

export interface Recommendation {
  action: Action;
  diagnosis: string;
  signature?: string;
  confidence: number;
  calibrated_confidence: number;
  reasoning: string;
  cited_incidents: string[];
  actions_known_to_fail: string[];
}

export interface MetricsRow {
  incident_id: string;
  ts: number;
  true_type: string;
  diagnosis: string | null;
  machine_id: string;
  memory_enabled: number;
  exposure: number;
  status: string;
  mttr_sim_s: number | null;
  human_wait_sim_s: number | null;
  agent_time_real_s: number | null;
  tool_calls: number | null;
  first_attempt_tool_calls: number | null;
  attempts: number | null;
  investigation_efficiency: number | null;
  llm_confidence: number | null;
  calibrated_confidence: number | null;
  memory_hit: number;
  top_match_same_class: number | null;
  recommendation_correct: number;
  first_time_right: number;
  false_replay: number;
  escalated: number;
  llm_tokens: number | null;
  cost_usd: number | null;
}

export interface MemoryRecord {
  document_id: string;
  incident_id: string;
  kind: "episode" | "lesson";
  text: string;
  metadata: string; // JSON
  created_ts: number;
  retain_status: "pending" | "retained" | "failed";
  error: string | null;
}

export interface UsageTotals {
  calls: number;
  prompt_tokens: number;
  cached_tokens: number;
  completion_tokens: number;
  reasoning_tokens: number;
  cost_usd: number;
  unpriced_calls: number;
  memory_tokens: number; // Hindsight's internal LLM tokens (informational)
  memory_billed_tokens: number; // estimated billable Hindsight tokens
  memory_cost_usd: number; // estimated
  memory_refreshes: number;
  total_tokens: number;
  total_cost_usd: number;
}

export interface UsageResponse {
  available: boolean;
  cap_usd?: number | null;
  all_time?: UsageTotals;
  session?: UsageTotals;
  by_model?: { provider: string; model: string; calls: number; tokens: number; cost_usd: number }[];
  pricing?: { llm: string; hindsight: string };
}

export interface CustomSpec {
  machine: string;
  config_changed: boolean;
  minutes_before: number;
  throughput_delta: number;
  error_rate: number;
  temperature: "normal" | "high";
  calibration: "fresh" | "old";
  network: "normal" | "degraded";
  memory_trend: "flat" | "climbing";
}

// ---- eval report (GET /api/eval/latest; backend/eval/report.py) ----

export interface EvalEstimate {
  mean: number;
  lo: number;
  hi: number;
  n: number;
}

export type EvalBucket = "1st" | "2nd" | "3rd+";
export type EvalMetric = "accuracy" | "mttr_min" | "tool_calls" | "first_attempt_tool_calls" | "confidence";

export interface EvalTarget {
  id: string;
  target: string;
  status: "PASS" | "FAIL" | "NO DATA";
  evidence: string;
}

export interface EvalSummary {
  rows: number;
  errors: number;
  seeds: number[];
  by_exposure: Record<EvalMetric, Record<EvalBucket, { memory_on: EvalEstimate | null; memory_off: EvalEstimate | null; diff: EvalEstimate | null }>>;
  targets: EvalTarget[];
  discrimination: Record<"memory_on" | "memory_off", { probes: { k: number; n: number } }>;
  retrieval: { recall_at_1: EvalEstimate | null; strong_precision: EvalEstimate | null };
  calibration: Record<"memory_on" | "memory_off", { brier: number | null }>;
  totals: { llm_usd: number; hindsight_usd: number; incidents: number };
}

export interface EvalLatest {
  run_id: string;
  path: string;
  metadata: Record<string, unknown> & { git_sha?: string; llm_primary?: string; started_at?: string; tool_call_sim_s?: number };
  summary: EvalSummary;
}
