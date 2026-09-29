"use client";

import { PREDEFINED, SITE_KNOWLEDGE } from "@/lib/types";

export type Tab = "floor" | "memory" | "learning";

export default function BottomBar({
  busy,
  busyReason,
  onPredefined,
  onRandom,
  onCustom,
  tab,
  setTab,
}: {
  busy: boolean;
  busyReason: string;
  onPredefined: (type: string) => void;
  onRandom: () => void;
  onCustom: () => void;
  tab: Tab;
  setTab: (t: Tab) => void;
}) {
  const why = busy ? busyReason : undefined;
  const base =
    "whitespace-nowrap rounded-md border px-2.5 py-2 font-mono text-xs font-semibold tracking-wider transition disabled:cursor-not-allowed disabled:opacity-35 2xl:px-3";
  return (
    <footer className="flex h-16 shrink-0 items-center gap-1.5 border-t border-border bg-panel px-4 2xl:gap-2">
      <span className="mr-1 font-mono text-[10px] tracking-widest text-muted">TRIGGER</span>
      {PREDEFINED.map((p) => (
        <button
          key={p.type}
          disabled={busy}
          title={why ?? `Inject a ${p.label.toLowerCase()} on a random machine`}
          onClick={() => onPredefined(p.type)}
          className={`${base} border-border bg-panel-2 text-text hover:border-muted`}
        >
          {p.label.toUpperCase()}
        </button>
      ))}
      <span className="ml-2 mr-0.5 whitespace-nowrap font-mono text-[10px] tracking-widest text-memory" title="Incidents whose fix only the plant's engineers know: the first one escalates, memory lets the agent fix the next one itself">
        ◆ SITE
      </span>
      {SITE_KNOWLEDGE.map((p) => (
        <button
          key={p.type}
          disabled={busy}
          title={why ?? `Inject a ${p.label.toLowerCase()}: ${p.hint}`}
          onClick={() => onPredefined(p.type)}
          className={`${base} border-memory/50 bg-panel-2 text-text hover:border-memory`}
        >
          {p.label.toUpperCase()}
        </button>
      ))}
      <button disabled={busy} title={why ?? "Build an incident from signals — the class stays hidden"} onClick={onCustom} className={`${base} border-border text-text hover:border-muted`}>
        CUSTOM…
      </button>
      <button disabled={busy} title={why ?? "A random incident class on a random machine — hidden until it closes"} onClick={onRandom} className={`${base} border-recovering/60 text-recovering hover:bg-recovering/10`}>
        SURPRISE ME
      </button>

      <nav className="ml-auto flex rounded-md border border-border p-0.5" aria-label="Views">
        {(["floor", "memory", "learning"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`whitespace-nowrap rounded px-3 py-1.5 font-mono text-xs font-semibold tracking-widest transition 2xl:px-4 ${tab === t ? "bg-panel-2 text-text" : "text-muted hover:text-text"}`}
            style={t === "memory" && tab === t ? { color: "var(--color-memory)" } : undefined}
          >
            {t === "floor" ? "FLOOR" : t === "memory" ? "◆ MEMORY" : "LEARNING"}
          </button>
        ))}
      </nav>
    </footer>
  );
}
