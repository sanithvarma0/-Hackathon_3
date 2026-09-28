"use client";

import { useEffect, useState } from "react";
import { getHealth, type DependencyStatus, type HealthReport } from "@/lib/api";

const POLL_MS = 10_000;

const STATUS_COLOR: Record<DependencyStatus, string> = {
  ok: "var(--color-healthy)",
  not_configured: "var(--color-muted)",
  unreachable: "var(--color-degraded)",
  auth_failed: "var(--color-critical)",
  error: "var(--color-critical)",
};

export default function HealthPanel() {
  const [report, setReport] = useState<HealthReport | null>(null);
  const [backendError, setBackendError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      getHealth()
        .then((r) => {
          if (!cancelled) {
            setReport(r);
            setBackendError(null);
          }
        })
        .catch((e: Error) => !cancelled && setBackendError(e.message));
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <section>
      <h2 className="mb-3 font-mono text-xs font-semibold tracking-widest text-muted">SYSTEM HEALTH</h2>
      {backendError && <p className="font-mono text-sm text-critical">Backend unreachable: {backendError}</p>}
      {!report && !backendError && <p className="font-mono text-sm text-muted">Checking…</p>}
      {report && (
        <ul className="space-y-2">
          {report.dependencies.map((d) => (
            <li key={d.name} className="flex items-center justify-between gap-4 font-mono text-sm">
              <span className="text-text">{d.name}</span>
              <span title={d.detail} style={{ color: STATUS_COLOR[d.status] }}>
                ● {d.status.replace("_", " ")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
