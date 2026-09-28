"use client";

import { useEffect, useRef, useState } from "react";
import type { IncidentRun, TraceKind } from "@/lib/reducer";

const KIND_STYLE: Record<TraceKind, { color: string; mark: string }> = {
  attempt: { color: "var(--color-text)", mark: "" },
  tool: { color: "var(--color-text)", mark: "▸" },
  memory: { color: "var(--color-memory)", mark: "◆" },
  guardrail: { color: "var(--color-degraded)", mark: "!" },
  system: { color: "var(--color-muted)", mark: "·" },
  error: { color: "var(--color-critical)", mark: "✖" },
  agent: { color: "var(--color-recovering)", mark: "→" },
};

export default function TracePanel({ run, memoryOn }: { run: IncidentRun | null; memoryOn: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const [follow, setFollow] = useState(true);
  const lines = run?.trace ?? [];

  // Auto-scroll, unless the viewer scrolled up to read.
  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines.length, follow]);

  return (
    <section className="flex min-h-[150px] flex-[1_1_0] flex-col rounded-lg border border-border bg-panel">
      <header className="flex items-center justify-between border-b border-border px-3 py-2">
        <h2 className="font-mono text-xs font-semibold tracking-widest text-muted">INVESTIGATION TRACE</h2>
        <span className="font-mono text-xs text-muted">
          {run ? `${run.id} · ${run.toolCalls} tool call${run.toolCalls === 1 ? "" : "s"}` : "idle"}
          {!follow && lines.length > 0 && (
            <button className="ml-2 text-recovering hover:underline" onClick={() => setFollow(true)}>
              ↓ follow
            </button>
          )}
        </span>
      </header>
      <div
        ref={box}
        onScroll={(e) => {
          const el = e.currentTarget;
          setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 24);
        }}
        className="scroll-thin min-h-0 flex-1 overflow-y-auto px-3 py-2 font-mono text-[14px] leading-6"
      >
        {lines.length === 0 && (
          <p className="text-muted">
            Trigger an incident below. Every step the agent takes streams here — tool calls, memory lookups
            <span style={{ color: memoryOn ? "var(--color-memory)" : "var(--color-memory-off)" }}> (purple)</span>, retries
            and the final decision.
          </p>
        )}
        {lines.map((l) => {
          const k = KIND_STYLE[l.kind];
          // Purple means memory at work; a skipped touchpoint is grey (BUILD_PLAN.md 10.1).
          const color = l.muted ? "var(--color-memory-off)" : k.color;
          if (l.kind === "attempt") {
            return (
              <div key={l.id} className="mt-2 border-t border-border pt-1 text-xs font-semibold tracking-widest text-muted first:mt-0 first:border-0">
                {l.text.toUpperCase()}
              </div>
            );
          }
          const body = (
            <span style={{ color }}>
              <span className="inline-block w-4 text-center">{k.mark}</span>
              {l.text}
              {l.ok === false && <span className="text-critical"> (failed)</span>}
            </span>
          );
          return l.detail ? (
            <details key={l.id} className="group">
              <summary className="cursor-pointer list-none truncate hover:bg-panel-2">
                {body}
                <span className="ml-1 text-xs text-muted group-open:hidden">[+]</span>
              </summary>
              <pre className="mb-1 ml-4 whitespace-pre-wrap break-words border-l border-border pl-2 text-[12px] leading-5 text-muted">
                {l.detail}
              </pre>
            </details>
          ) : (
            <div key={l.id} className="break-words">
              {body}
            </div>
          );
        })}
      </div>
    </section>
  );
}
