"use client";

import { Bar, BarChart, CartesianGrid, LabelList, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "@/lib/api";
import { fmtDuration } from "@/lib/reducer";
import { useState } from "react";
import EvalReport from "@/components/tabs/EvalReport";
import { useApi } from "@/lib/useMemoryOps";
import type { MetricsRow } from "@/lib/types";

const ON = "#a371f7";
const OFF = "#6e7681";
const BUCKETS = ["1st", "2nd", "3rd+"] as const;

const bucketOf = (exposure: number) => BUCKETS[Math.min(exposure, 3) - 1];

interface Cell {
  bucket: string;
  on: number | null;
  off: number | null;
  nOn: number;
  nOff: number;
}

/** Mean of `pick` per exposure bucket, memory ON vs OFF. Only measured rows; empty cells stay empty. */
function aggregate(rows: MetricsRow[], pick: (r: MetricsRow) => number | null): Cell[] {
  return BUCKETS.map((bucket) => {
    const cell: Cell = { bucket, on: null, off: null, nOn: 0, nOff: 0 };
    for (const side of ["on", "off"] as const) {
      const vals = rows
        .filter((r) => bucketOf(r.exposure) === bucket && (r.memory_enabled === 1) === (side === "on"))
        .map(pick)
        .filter((v): v is number => v != null);
      if (vals.length) cell[side] = vals.reduce((a, b) => a + b, 0) / vals.length;
      cell[side === "on" ? "nOn" : "nOff"] = vals.length;
    }
    return cell;
  });
}

function Chart({
  title,
  data,
  unit,
  fmt,
  axis = fmt,
}: {
  title: string;
  data: Cell[];
  unit: string;
  fmt: (v: number) => string;
  axis?: (v: number) => string;
}) {
  const hasData = data.some((c) => c.on != null || c.off != null);
  return (
    <section className="flex min-h-0 flex-col rounded-lg border border-border bg-panel p-4">
      <h3 className="font-mono text-xs font-semibold tracking-widest text-muted">{title}</h3>
      <p className="mb-2 font-mono text-[11px] text-muted">by how many times this incident class had been seen · {unit}</p>
      {hasData ? (
        <div className="min-h-0 flex-1">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 22, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#1f2732" vertical={false} />
              <XAxis dataKey="bucket" stroke="#8b949e" tick={{ fontFamily: "monospace", fontSize: 12 }} />
              <YAxis stroke="#8b949e" tick={{ fontFamily: "monospace", fontSize: 11 }} tickFormatter={(v: number) => axis(v)} width={56} />
              <Tooltip
                cursor={{ fill: "#161c25" }}
                contentStyle={{ background: "#161c25", border: "1px solid #232b36", fontFamily: "monospace", fontSize: 12 }}
                formatter={(v, name, item) => {
                  const cell = item.payload as Cell;
                  const n = name === "memory ON" ? cell.nOn : cell.nOff;
                  return [`${fmt(Number(v))} (n=${n})`, name];
                }}
              />
              <Legend wrapperStyle={{ fontFamily: "monospace", fontSize: 12 }} />
              <Bar dataKey="on" name="memory ON" fill={ON} radius={[3, 3, 0, 0]} maxBarSize={64}>
                <LabelList dataKey="nOn" position="top" formatter={(n) => (Number(n) ? `n=${n}` : "")} fill="#8b949e" fontSize={11} />
              </Bar>
              <Bar dataKey="off" name="memory OFF" fill={OFF} radius={[3, 3, 0, 0]} maxBarSize={64}>
                <LabelList dataKey="nOff" position="top" formatter={(n) => (Number(n) ? `n=${n}` : "")} fill="#8b949e" fontSize={11} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center text-sm text-muted">No incidents yet</div>
      )}
    </section>
  );
}

export default function Learning({ version }: { version: number }) {
  const evalReport = useApi(api.evalLatest, 0);
  const [view, setView] = useState<"eval" | "live" | null>(null);
  const shown = view ?? (evalReport.data ? "eval" : "live");

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4">
      <header className="flex items-end justify-between">
        <div>
          <h2 className="font-mono text-lg font-semibold tracking-widest text-text">IS THE AGENT LEARNING?</h2>
          <p className="text-sm text-muted">
            {shown === "eval"
              ? "The committed evaluation: the same agent on identical incident sequences with memory ON and OFF, scored against the simulator's ground truth."
              : "Live incidents from this bank, scored against the simulator's ground truth. Small samples: anecdotes, not results."}
          </p>
        </div>
        <div className="flex rounded-md border border-border p-0.5" role="tablist">
          {(["eval", "live"] as const).map((v) => (
            <button
              key={v}
              role="tab"
              aria-selected={shown === v}
              onClick={() => setView(v)}
              className={`whitespace-nowrap rounded px-3 py-1.5 font-mono text-xs font-semibold tracking-widest ${shown === v ? "bg-panel-2 text-text" : "text-muted hover:text-text"}`}
            >
              {v === "eval" ? "EVAL REPORT" : "THIS SESSION"}
            </button>
          ))}
        </div>
      </header>

      {shown === "eval" ? (
        evalReport.data ? (
          <EvalReport report={evalReport.data} />
        ) : (
          <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted">
            {evalReport.error?.includes("no eval report") || evalReport.error?.includes("HTTP 404")
              ? "No evaluation report yet — run `make eval`."
              : evalReport.error
                ? `Could not load the eval report: ${evalReport.error}`
                : "Loading the eval report…"}
          </div>
        )
      ) : (
        <LiveSession version={version} />
      )}
    </div>
  );
}

function LiveSession({ version }: { version: number }) {
  const { data, error } = useApi(api.metrics, version);
  const rows = data ?? [];

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <p className="font-mono text-xs text-muted">n = {rows.length} live incidents</p>
      {error && <p className="font-mono text-sm text-critical">Could not load metrics: {error}</p>}

      <div className="grid min-h-0 flex-1 grid-cols-3 gap-4">
        <Chart title="FIRST RECOMMENDATION CORRECT" unit="share of incidents" data={aggregate(rows, (r) => r.recommendation_correct)} fmt={(v) => `${Math.round(v * 100)}%`} />
        <Chart title="MTTR (SIM)" unit="mean, lower is better" data={aggregate(rows, (r) => r.mttr_sim_s)} fmt={(v) => fmtDuration(v)} axis={(v) => `${(v / 60).toFixed(v < 600 ? 1 : 0)}m`} />
        <Chart title="TOOL CALLS TO DIAGNOSE" unit="first attempt, lower is better" data={aggregate(rows, (r) => r.first_attempt_tool_calls)} fmt={(v) => v.toFixed(1)} />
      </div>

      <section className="max-h-[38%] min-h-0 overflow-y-auto rounded-lg border border-border bg-panel scroll-thin">
        <table className="w-full font-mono text-[13px]">
          <thead className="sticky top-0 bg-panel text-left text-[11px] uppercase tracking-widest text-muted">
            <tr>
              {["incident", "class", "seen", "memory", "match", "first rec", "outcome", "MTTR", "tools", "tokens", "cost"].map((h) => (
                <th key={h} className="px-3 py-2 font-normal">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {[...rows].reverse().map((r) => (
              <tr key={r.incident_id} className="border-t border-border">
                <td className="px-3 py-1.5 text-text">{r.incident_id}</td>
                <td className="px-3 py-1.5 text-muted">{r.true_type.replace(/_/g, " ")}</td>
                <td className="px-3 py-1.5 text-muted">{bucketOf(r.exposure)}</td>
                <td className="px-3 py-1.5" style={{ color: r.memory_enabled ? ON : OFF }}>{r.memory_enabled ? "ON" : "OFF"}</td>
                <td className="px-3 py-1.5" style={{ color: r.memory_hit ? ON : OFF }}>
                  {r.memory_hit ? (r.top_match_same_class ? "same class" : "other class") : "—"}
                </td>
                <td className="px-3 py-1.5" style={{ color: r.recommendation_correct ? "var(--color-healthy)" : "var(--color-critical)" }}>
                  {r.recommendation_correct ? "correct" : r.false_replay ? "false replay" : "wrong"}
                </td>
                <td className="px-3 py-1.5 text-muted">{r.status}</td>
                <td className="px-3 py-1.5 text-text">{fmtDuration(r.mttr_sim_s)}</td>
                <td className="px-3 py-1.5 text-text">{r.tool_calls ?? "—"}</td>
                <td className="px-3 py-1.5 text-muted">{r.llm_tokens?.toLocaleString() ?? "—"}</td>
                <td className="px-3 py-1.5 text-muted">{r.cost_usd != null ? `$${r.cost_usd.toFixed(4)}` : "—"}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={11} className="px-3 py-4 text-center text-muted">
                  Every resolved or escalated incident adds a row here.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
