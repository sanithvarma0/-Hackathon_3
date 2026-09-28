"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useMemoryOps";
import type { MemoryRecord } from "@/lib/types";

const fmtDate = (ts: number) => new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC";

function parseMeta(r: MemoryRecord): Record<string, string> {
  try {
    return JSON.parse(r.metadata);
  } catch {
    return {};
  }
}

const RETAIN: Record<MemoryRecord["retain_status"], { label: string; color: string }> = {
  retained: { label: "in Hindsight", color: "var(--color-memory)" },
  pending: { label: "pending sync", color: "var(--color-degraded)" },
  failed: { label: "retrying", color: "var(--color-critical)" },
};

export default function MemoryBrowser({
  version,
  focusId,
  runbookLive,
}: {
  version: number;
  focusId: string | null;
  runbookLive: { content: string; ts: number } | null;
}) {
  const records = useApi(api.memoryRecords, version);
  const metrics = useApi(api.metrics, version);
  const runbook = useApi(api.runbook, version);
  const [open, setOpen] = useState<string | null>(focusId); // the tab mounts fresh per visit
  const rowRefs = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    if (!focusId) return;
    rowRefs.current[focusId]?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focusId, records.data]);

  const classOf = Object.fromEntries((metrics.data ?? []).map((m) => [m.incident_id, m.true_type]));
  const list = [...(records.data ?? [])].reverse(); // newest first
  const rawRunbook = runbookLive?.content ?? runbook.data?.content ?? null;
  // Hindsight answers with a placeholder while the mental model is being (re)built.
  const consolidating = !!rawRunbook && /^generating content/i.test(rawRunbook.trim());
  const runbookText = consolidating ? null : rawRunbook;

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-4 p-4">
      <section className="flex min-h-0 flex-col rounded-lg border border-border bg-panel">
        <header className="flex items-baseline justify-between border-b border-border px-4 py-3">
          <h2 className="font-mono text-sm font-semibold tracking-widest text-memory">◆ MEMORY BROWSER</h2>
          <span className="font-mono text-xs text-muted">
            {list.length} record{list.length === 1 ? "" : "s"} · exactly what was retained to Hindsight
          </span>
        </header>
        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
          {records.error && <p className="p-4 font-mono text-sm text-critical">Could not load memory: {records.error}</p>}
          {!records.error && list.length === 0 && (
            <p className="p-4 text-sm text-muted">
              Memory is empty. Resolve an incident and its episode appears here — the same text the agent will recall next time.
            </p>
          )}
          {list.map((r) => {
            const meta = parseMeta(r);
            const expanded = open === r.document_id || (open === r.incident_id && r.kind === "episode");
            const retain = RETAIN[r.retain_status];
            return (
              <div
                key={r.document_id}
                ref={(el) => {
                  if (r.kind === "episode") rowRefs.current[r.incident_id] = el;
                }}
                className={`border-b border-border ${expanded ? "bg-panel-2" : ""}`}
              >
                <button
                  onClick={() => setOpen(expanded ? null : r.document_id)}
                  className="grid w-full grid-cols-[100px_80px_minmax(0,1fr)_180px_100px] items-center gap-3 px-4 py-2.5 text-left font-mono text-[13px] hover:bg-panel-2"
                >
                  <span className="font-semibold text-text">{r.incident_id}</span>
                  <span
                    className="w-fit rounded px-1.5 py-0.5 text-[11px] uppercase tracking-wider"
                    style={{ color: "var(--color-memory)", background: "#a371f71f" }}
                  >
                    {r.kind}
                  </span>
                  <span className="truncate text-muted">
                    {r.kind === "episode" ? (
                      <>
                        <span className="text-text">{(classOf[r.incident_id] ?? meta.diagnosis ?? "").replace(/_/g, " ")}</span> ·{" "}
                        {meta.machine_id} · {meta.final_action?.replace(/_/g, " ")} → {meta.outcome}
                      </>
                    ) : (
                      <>
                        {meta.machine_id} · {meta.failed_action?.replace(/_/g, " ")} → {meta.effect?.replace(/_/g, " ")}
                      </>
                    )}
                  </span>
                  <span className="text-muted">{fmtDate(r.created_ts)}</span>
                  <span className="text-right text-[11px]" style={{ color: retain.color }} title={r.error ?? undefined}>
                    {retain.label}
                  </span>
                </button>
                {expanded && (
                  <pre className="mx-4 mb-3 max-h-96 overflow-y-auto whitespace-pre-wrap break-words rounded border border-[#a371f733] bg-bg p-3 font-mono text-[12.5px] leading-5 text-text scroll-thin">
                    {r.text}
                  </pre>
                )}
              </div>
            );
          })}
        </div>
      </section>

      <section className="flex min-h-0 flex-col rounded-lg border border-[#a371f755] bg-panel">
        <header className="border-b border-[#a371f733] px-4 py-3">
          <h2 className="font-mono text-sm font-semibold tracking-widest text-memory">◆ THE RUNBOOK THE AGENT WROTE ITSELF</h2>
          <p className="mt-0.5 text-xs text-muted">
            Hindsight&apos;s &ldquo;Incident Patterns&rdquo; mental model — rebuilt from memory after each consolidation
            {runbookLive ? ` · updated ${new Date(runbookLive.ts * 1000).toLocaleTimeString()}` : ""}
          </p>
        </header>
        <div className={`scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-3 ${runbookLive ? "memory-in" : ""}`} key={runbookLive?.ts ?? "static"}>
          {runbookText ? (
            <Markdownish text={runbookText} />
          ) : consolidating ? (
            <p className="text-sm text-memory">◆ Hindsight is consolidating the latest episodes into the runbook…</p>
          ) : (
            <p className="text-sm text-muted">
              Nothing yet. After a few incidents Hindsight consolidates their episodes into patterns: signature, fastest evidence, the fix
              that works, and the fixes that don&apos;t.
            </p>
          )}
        </div>
      </section>
    </div>
  );
}

/** Tiny renderer for the headings, bullets and bold the mental model produces — no HTML injection. */
function Markdownish({ text }: { text: string }) {
  const inline = (s: string) =>
    s.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
      part.startsWith("**") && part.endsWith("**") ? (
        <strong key={i} className="text-text">
          {part.slice(2, -2)}
        </strong>
      ) : (
        <span key={i}>{part}</span>
      ),
    );
  return (
    <div className="space-y-2 text-sm leading-6 text-muted">
      {text.split("\n").map((line, i) => {
        const t = line.trim();
        if (!t) return null;
        if (t.startsWith("### ")) return <h4 key={i} className="pt-1 font-mono text-xs font-semibold uppercase tracking-widest text-memory">{t.slice(4)}</h4>;
        if (t.startsWith("## ")) return <h3 key={i} className="pt-2 font-mono text-sm font-semibold text-memory">{t.slice(3)}</h3>;
        if (t.startsWith("# ")) return <h3 key={i} className="font-mono text-sm font-semibold text-memory">{t.slice(2)}</h3>;
        if (/^[-*] /.test(t)) return <p key={i} className="pl-4 -indent-3">◆ {inline(t.slice(2))}</p>;
        return <p key={i}>{inline(t)}</p>;
      })}
    </div>
  );
}
