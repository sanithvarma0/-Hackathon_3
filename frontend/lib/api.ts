import type {
  Action,
  BusEvent,
  CustomSpec,
  EvalLatest,
  IncidentView,
  MemoryRecord,
  MetricsRow,
  StateResponse,
  UsageResponse,
} from "@/lib/types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type DependencyStatus = "ok" | "not_configured" | "unreachable" | "auth_failed" | "error";

export interface DependencyHealth {
  name: string;
  status: DependencyStatus;
  detail: string;
}

export interface HealthReport {
  ok: boolean;
  dependencies: DependencyHealth[];
}

/** A refusal from the backend: every one is `{code, message}` (BUILD_PLAN.md 9). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

// The public deployment asks for a demo passcode on every action (docs/DEPLOY.md).
const PASSCODE_KEY = "memoryops.passcode";

function storedPasscode(): string {
  try {
    return localStorage.getItem(PASSCODE_KEY) ?? "";
  } catch {
    return "";
  }
}

function askPasscode(): string | null {
  if (typeof window === "undefined") return null;
  const given = window.prompt("This demo is protected. Enter the demo passcode:");
  if (!given) return null;
  try {
    localStorage.setItem(PASSCODE_KEY, given);
  } catch {
    // private window: the passcode lasts for this request only
  }
  return given;
}

async function request<T>(path: string, init?: RequestInit, passcode = storedPasscode()): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      ...init,
      headers: {
        "content-type": "application/json",
        ...(passcode ? { "x-demo-passcode": passcode } : {}),
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError(0, "BACKEND_UNREACHABLE", `Cannot reach the backend at ${API_BASE}`);
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    if (body.code === "PASSCODE_REQUIRED") {
      const given = askPasscode();
      if (given) return request<T>(path, init, given);
    }
    if (typeof body.code === "string") throw new ApiError(res.status, body.code, body.message);
    if (res.status === 422) throw new ApiError(422, "INVALID_REQUEST", "The request was rejected");
    throw new ApiError(res.status, "HTTP_ERROR", `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });

export const api = {
  health: () => request<HealthReport>("/api/health"),
  state: () => request<StateResponse>("/api/state"),
  history: (minutes = 10) =>
    request<Record<string, { ts: number; value: number }[]>>(
      `/api/plant/history?metric=throughput_pct&minutes=${minutes}`,
    ),
  incident: (id: string) =>
    request<{
      incident: IncidentView;
      pending: Record<string, unknown> | null;
      events: BusEvent[];
      memory_records: MemoryRecord[];
      metrics: MetricsRow | null;
    }>(`/api/incidents/${id}`),
  incidents: () => request<IncidentView[]>("/api/incidents"),
  triggerPredefined: (type: string, memoryEnabled: boolean) =>
    post<IncidentView>("/api/incident/predefined", { type, memory_enabled: memoryEnabled }),
  triggerRandom: (memoryEnabled: boolean) =>
    post<IncidentView>("/api/incident/random", { memory_enabled: memoryEnabled }),
  triggerCustom: (spec: CustomSpec, memoryEnabled: boolean) =>
    post<IncidentView>("/api/incident/custom", { spec, memory_enabled: memoryEnabled }),
  act: (id: string, action: Action) => post(`/api/incident/${id}/action`, { action }),
  ignore: (id: string) => post(`/api/incident/${id}/ignore`),
  memoryRecords: () => request<MemoryRecord[]>("/api/memory/records"),
  runbook: () => request<{ bank_id: string; content: string | null }>("/api/memory/runbook"),
  metrics: () => request<MetricsRow[]>("/api/metrics"),
  evalLatest: () => request<EvalLatest>("/api/eval/latest"),
  usage: () => request<UsageResponse>("/api/usage"),
  reset: (bank: "live" | "seeded", wipeMemory: boolean) =>
    post("/api/admin/reset", { bank, wipe_memory: wipeMemory }),
};

export const streamUrl = (since: number) => `${API_BASE}/api/stream?since=${since}`;

// Kept for the health panel.
export const getHealth = api.health;
