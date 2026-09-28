"use client";

import { useEffect } from "react";
import type { Toast, ToastTone } from "@/lib/reducer";

const TONE: Record<ToastTone, string> = {
  memory: "var(--color-memory)",
  critical: "var(--color-critical)",
  degraded: "var(--color-degraded)",
  healthy: "var(--color-healthy)",
  info: "var(--color-recovering)",
};

const TTL_MS = 7000;

function ToastItem({ toast, dismiss }: { toast: Toast; dismiss: (id: number) => void }) {
  useEffect(() => {
    const t = setTimeout(() => dismiss(toast.id), TTL_MS);
    return () => clearTimeout(t);
  }, [toast.id, dismiss]);
  const color = TONE[toast.tone];
  return (
    <div
      role="status"
      className={`slide-in pointer-events-auto w-96 rounded-md border-l-4 bg-panel-2 px-3 py-2 shadow-xl ${toast.tone === "memory" ? "memory-in" : ""}`}
      style={{ borderColor: color }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="font-mono text-sm font-semibold" style={{ color }}>
          {toast.tone === "memory" ? "◆ " : ""}
          {toast.title}
        </div>
        <button onClick={() => dismiss(toast.id)} className="text-muted hover:text-text" aria-label="Dismiss">
          ×
        </button>
      </div>
      {toast.body && <div className="mt-0.5 line-clamp-2 text-[13px] text-muted">{toast.body}</div>}
    </div>
  );
}

export default function Toasts({ toasts, dismiss }: { toasts: Toast[]; dismiss: (id: number) => void }) {
  return (
    <div className="pointer-events-none absolute bottom-3 left-3 z-20 flex flex-col gap-2">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} dismiss={dismiss} />
      ))}
    </div>
  );
}
