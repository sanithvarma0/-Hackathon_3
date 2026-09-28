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

export async function getHealth(): Promise<HealthReport> {
  const res = await fetch(`${API_BASE}/api/health`, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
