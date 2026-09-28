"use client";

import { useEffect, useState } from "react";
import { getHealth, type DependencyStatus, type HealthReport } from "@/lib/api";

const POLL_MS = 10_000;

const STATUS_STYLE: Record<DependencyStatus, string> = {
  ok: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  not_configured: "bg-zinc-500/15 text-zinc-300 ring-zinc-500/30",
  unreachable: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  auth_failed: "bg-red-500/15 text-red-300 ring-red-500/30",
  error: "bg-red-500/15 text-red-300 ring-red-500/30",
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
    <section className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-5">
      <h2 className="mb-4 font-mono text-xs uppercase tracking-widest text-zinc-400">
        System health
      </h2>
      {backendError && (
        <p className="font-mono text-sm text-red-300">Backend unreachable: {backendError}</p>
      )}
      {!report && !backendError && <p className="font-mono text-sm text-zinc-500">Checking…</p>}
      {report && (
        <ul className="space-y-2">
          {report.dependencies.map((d) => (
            <li key={d.name} className="flex items-center justify-between gap-4 font-mono text-sm">
              <span className="text-zinc-200">{d.name}</span>
              <span
                title={d.detail}
                className={`rounded px-2 py-0.5 text-xs ring-1 ${STATUS_STYLE[d.status]}`}
              >
                {d.status}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
