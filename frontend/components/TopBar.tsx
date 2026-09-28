"use client";

import { useEffect, useRef, useState } from "react";
import HealthPanel from "@/components/HealthPanel";
import { api } from "@/lib/api";
import type { Connection } from "@/lib/reducer";
import { useApi } from "@/lib/useMemoryOps";
import type { PlantState } from "@/lib/types";

const CONNECTION: Record<Connection, { color: string; label: string }> = {
  live: { color: "var(--color-healthy)", label: "LIVE" },
  connecting: { color: "var(--color-muted)", label: "CONNECTING" },
  reconnecting: { color: "var(--color-degraded)", label: "RECONNECTING" },
  offline: { color: "var(--color-critical)", label: "OFFLINE" },
};

function simClock(ts: number): string {
  return new Date(ts * 1000).toISOString().slice(11, 19);
}

const fmtTokens = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n));

export default function TopBar({
  plant,
  connection,
  bankId,
  simSpeed,
  memoryOn,
  setMemoryOn,
  usageVersion,
  incidentActive,
  onReset,
}: {
  plant: PlantState | null;
  connection: Connection;
  bankId: string | null;
  simSpeed: number | null;
  memoryOn: boolean;
  setMemoryOn: (on: boolean) => void;
  usageVersion: number;
  incidentActive: boolean;
  onReset: (wipe: boolean) => void;
}) {
  const usage = useApi(api.usage, usageVersion, 30_000).data;
  const conn = CONNECTION[connection];

  return (
    <header className="flex h-16 shrink-0 items-center gap-6 border-b border-border bg-panel px-5">
      <div className="flex items-baseline gap-3">
        <span className="font-mono text-lg font-bold tracking-[0.2em] text-text">MEMORYOPS</span>
        <span className="font-mono text-xs tracking-widest text-muted">SECTOR 7 · LINE A</span>
      </div>

      <div className="flex items-center gap-5 border-l border-border pl-5">
        <Kpi label="OEE" value={plant ? `${plant.oee_pct.toFixed(1)}%` : "—"} />
        <Kpi label="LINE OUTPUT" value={plant ? `${plant.line_throughput_pct.toFixed(1)}%` : "—"} warn={!!plant && plant.line_throughput_pct < 88} />
        <Kpi label="ALERTS" value={plant ? String(plant.alerts) : "—"} alert={!!plant && plant.alerts > 0} />
        <Kpi label={`SIM CLOCK${simSpeed ? ` ×${simSpeed}` : ""}`} value={plant ? simClock(plant.ts) : "—"} />
      </div>

      <div className="ml-auto flex items-center gap-4">
        <span className="flex items-center gap-1.5 font-mono text-xs" style={{ color: conn.color }} title="Server-sent events stream">
          <span className={connection === "live" ? "" : "flicker"}>●</span>
          {conn.label}
        </span>
        <span className="font-mono text-xs text-muted" title="Hindsight memory bank">
          BANK <span className="text-text">{bankId ?? "—"}</span>
        </span>
        {usage?.available && usage.session && (
          <span
            className="font-mono text-xs text-muted"
            title={[
              `This run: ${usage.session.calls} LLM calls, ${fmtTokens(usage.session.total_tokens)} tokens, $${usage.session.cost_usd.toFixed(4)}`,
              `All time: ${usage.all_time?.calls} calls, ${fmtTokens(usage.all_time?.total_tokens ?? 0)} tokens, $${usage.all_time?.cost_usd.toFixed(4)}`,
              `Memory (retain) tokens this run: ${fmtTokens(usage.session.memory_tokens)}`,
              usage.cap_usd ? `Spend cap: $${usage.cap_usd} — beyond it the agent escalates instead of calling the LLM` : "",
            ]
              .filter(Boolean)
              .join("\n")}
          >
            SPEND <span className="text-text">${usage.session.cost_usd.toFixed(3)}</span> · {fmtTokens(usage.session.total_tokens)} tok
          </span>
        )}
        <MemoryToggle on={memoryOn} set={setMemoryOn} />
        <Overflow incidentActive={incidentActive} onReset={onReset} />
      </div>
    </header>
  );
}

function Kpi({ label, value, warn, alert }: { label: string; value: string; warn?: boolean; alert?: boolean }) {
  const color = alert ? "var(--color-critical)" : warn ? "var(--color-degraded)" : "var(--color-text)";
  return (
    <div className="leading-tight">
      <div className="font-mono text-[10px] tracking-widest text-muted">{label}</div>
      <div className="font-mono text-base font-semibold" style={{ color }}>
        {value}
      </div>
    </div>
  );
}

function MemoryToggle({ on, set }: { on: boolean; set: (on: boolean) => void }) {
  return (
    <button
      role="switch"
      aria-checked={on}
      onClick={() => set(!on)}
      title="Applies to the next incident you trigger. OFF = the agent reasons from evidence only."
      className="flex items-center gap-2 rounded-md border px-3 py-1.5 font-mono text-xs font-semibold tracking-widest transition"
      style={{
        borderColor: on ? "var(--color-memory)" : "var(--color-border)",
        color: on ? "var(--color-memory)" : "var(--color-memory-off)",
        background: on ? "#a371f71a" : "transparent",
      }}
    >
      ◆ MEMORY
      <span className="relative inline-block h-4 w-8 rounded-full" style={{ background: on ? "var(--color-memory)" : "#30363d" }}>
        <span className="absolute top-0.5 h-3 w-3 rounded-full bg-[#0b0f14] transition-all" style={{ left: on ? 18 : 2 }} />
      </span>
      {on ? "ON" : "OFF"}
    </button>
  );
}

function Overflow({ incidentActive, onReset }: { incidentActive: boolean; onReset: (wipe: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState<null | "reset" | "wipe">(null);
  const [health, setHealth] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(!open)} className="rounded px-2 py-1 font-mono text-lg leading-none text-muted hover:bg-panel-2 hover:text-text" aria-label="More">
        ⋯
      </button>
      {open && (
        <div className="absolute right-0 top-9 z-30 w-64 rounded-md border border-border bg-panel-2 p-1 shadow-xl">
          <MenuItem onClick={() => { setHealth(true); setOpen(false); }}>System health</MenuItem>
          <MenuItem onClick={() => { setConfirm("reset"); setOpen(false); }}>Reset plant (keep memory)</MenuItem>
          <MenuItem danger onClick={() => { setConfirm("wipe"); setOpen(false); }}>Wipe memory &amp; start fresh</MenuItem>
        </div>
      )}
      {confirm && (
        <Modal onClose={() => setConfirm(null)}>
          <h3 className="font-mono text-sm font-semibold text-text">{confirm === "wipe" ? "Wipe memory and start fresh?" : "Reset the plant?"}</h3>
          <p className="mt-2 text-sm text-muted">
            {confirm === "wipe"
              ? "Deletes every episode and lesson in the live Hindsight bank, the learning curve and the event history. Incident numbering restarts at INC-001. This cannot be undone."
              : "Machines return to healthy and the stream starts clean. Memory, metrics and incident numbering are kept."}
            {incidentActive && " The active incident will be abandoned."}
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <button onClick={() => setConfirm(null)} className="rounded border border-border px-3 py-1.5 font-mono text-xs text-text">
              Cancel
            </button>
            <button
              onClick={() => {
                onReset(confirm === "wipe");
                setConfirm(null);
              }}
              className="rounded px-3 py-1.5 font-mono text-xs font-semibold text-[#0b0f14]"
              style={{ background: confirm === "wipe" ? "var(--color-critical)" : "var(--color-degraded)" }}
            >
              {confirm === "wipe" ? "Wipe memory" : "Reset"}
            </button>
          </div>
        </Modal>
      )}
      {health && (
        <Modal onClose={() => setHealth(false)}>
          <HealthPanel />
        </Modal>
      )}
    </div>
  );
}

function MenuItem({ children, onClick, danger }: { children: React.ReactNode; onClick: () => void; danger?: boolean }) {
  return (
    <button onClick={onClick} className={`block w-full rounded px-3 py-2 text-left text-sm hover:bg-panel ${danger ? "text-critical" : "text-text"}`}>
      {children}
    </button>
  );
}

export function Modal({ children, onClose, wide }: { children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60" onMouseDown={onClose}>
      <div
        className={`slide-in rounded-lg border border-border bg-panel p-5 shadow-2xl ${wide ? "w-[640px]" : "w-[440px]"}`}
        onMouseDown={(e) => e.stopPropagation()}
        role="dialog"
      >
        {children}
      </div>
    </div>
  );
}
