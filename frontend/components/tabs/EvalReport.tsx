"use client";

import { Bar, BarChart, CartesianGrid, ErrorBar, LabelList, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { EvalBucket, EvalEstimate, EvalLatest, EvalMetric } from "@/lib/types";

const ON = "#a371f7";
const OFF = "#6e7681";
const BUCKETS: EvalBucket[] = ["1st", "2nd", "3rd+"];

const STATUS_STYLE = {
  PASS: { color: "var(--color-healthy)", mark: "✓" },
  FAIL: { color: "var(--color-critical)", mark: "✖" },
  "NO DATA": { color: "var(--color-muted)", mark: "–" },
} as const;

interface Point {
  bucket: string;
  on: number | null;
  off: number | null;
  onErr: [number, number];
  offErr: [number, number];
  nOn: number;
  nOff: number;
  diff: EvalEstimate | null;
}

const err = (e: EvalEstimate | null): [number, number] => (e ? [e.mean - e.lo, e.hi - e.mean] : [0, 0]);

function points(report: EvalLatest, metric: EvalMetric, scale = 1): Point[] {
  return BUCKETS.map((b) => {
    const cell = report.summary.by_exposure[metric][b];
    const s = (e: EvalEstimate | null) => (e ? { mean: e.mean * scale, lo: e.lo * scale, hi: e.hi * scale, n: e.n } : null);
    const on = s(cell.memory_on);
    const off = s(cell.memory_off);
    return {
      bucket: b,
      on: on?.mean ?? null,
      off: off?.mean ?? null,
      onErr: err(on),
      offErr: err(off),
      nOn: on?.n ?? 0,
      nOff: off?.n ?? 0,
      diff: s(cell.diff),
    };
  });
}

function MetricChart({
  title,
  unit,
  data,
  fmt,
}: {
  title: string;
  unit: string;
  data: Point[];
  fmt: (v: number) => string;
}) {
  return (
    <section className="flex min-h-0 flex-col rounded-lg border border-border bg-panel p-4">
      <h3 className="font-mono text-xs font-semibold tracking-widest text-muted">{title}</h3>
      <p className="mb-1 font-mono text-[11px] text-muted">{unit} · mean with 95% CI</p>
      <div className="min-h-0 flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 20, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid stroke="#1f2732" vertical={false} />
            <XAxis dataKey="bucket" stroke="#8b949e" tick={{ fontFamily: "monospace", fontSize: 12 }} />
            <YAxis stroke="#8b949e" tick={{ fontFamily: "monospace", fontSize: 11 }} tickFormatter={(v: number) => fmt(v)} width={52} />
            <Tooltip
              cursor={{ fill: "#161c25" }}
              contentStyle={{ background: "#161c25", border: "1px solid #232b36", fontFamily: "monospace", fontSize: 12 }}
              formatter={(v, name, item) => {
                const p = item.payload as Point;
                const [lo, hi] = name === "memory ON" ? p.onErr : p.offErr;
                const n = name === "memory ON" ? p.nOn : p.nOff;
                return [`${fmt(Number(v))} [${fmt(Number(v) - lo)}–${fmt(Number(v) + hi)}] n=${n}`, name];
              }}
            />
            <Legend wrapperStyle={{ fontFamily: "monospace", fontSize: 12 }} />
            <Bar dataKey="on" name="memory ON" fill={ON} radius={[3, 3, 0, 0]} maxBarSize={56}>
              <ErrorBar dataKey="onErr" width={6} stroke="#e6edf3" strokeWidth={1.2} />
            </Bar>
            <Bar dataKey="off" name="memory OFF" fill={OFF} radius={[3, 3, 0, 0]} maxBarSize={56}>
              <ErrorBar dataKey="offErr" width={6} stroke="#e6edf3" strokeWidth={1.2} />
              <LabelList dataKey="nOff" position="insideBottom" formatter={(n) => (Number(n) ? `n=${n}` : "")} fill="#0b0f14" fontSize={10} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-1 font-mono text-[11px] text-muted">
        ON − OFF at 3rd+:{" "}
        {(() => {
          const d = data[2].diff;
          if (!d) return "no data";
          const sig = d.lo > 0 || d.hi < 0;
          return (
            <span style={{ color: sig ? "var(--color-text)" : undefined }}>
              {d.mean >= 0 ? "+" : ""}
              {fmt(d.mean)} [{fmt(d.lo)}, {fmt(d.hi)}] {sig ? "· CI excludes 0" : "· CI includes 0"}
            </span>
          );
        })()}
      </p>
    </section>
  );
}

export default function EvalReport({ report }: { report: EvalLatest }) {
  const s = report.summary;
  const m = report.metadata;
  const pct = (v: number) => `${Math.round(v * 100)}%`;
  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex items-start justify-between gap-4">
        <p className="text-sm text-muted">
          <span className="font-mono text-text">{report.run_id}</span> · {s.rows} incident runs · seeds {s.seeds.join(", ")} · memory ON vs OFF paired on identical
          incidents · model <span className="font-mono">{String(m.llm_primary ?? "?")}</span> · ${(s.totals.llm_usd + s.totals.hindsight_usd).toFixed(2)} · full report in{" "}
          <span className="font-mono text-text">{report.path}/REPORT.md</span>
        </p>
      </div>

      <div className="grid grid-cols-5 gap-2">
        {s.targets.map((t) => {
          const st = STATUS_STYLE[t.status];
          return (
            <div key={t.id} className="rounded-md border bg-panel px-3 py-2" style={{ borderColor: st.color }} title={`${t.target}\n${t.evidence}`}>
              <div className="flex items-center justify-between font-mono text-[11px]">
                <span className="text-muted">TARGET {t.id}</span>
                <span style={{ color: st.color }}>
                  {st.mark} {t.status}
                </span>
              </div>
              <p className="mt-1 line-clamp-2 text-[12px] leading-4 text-text">{t.target}</p>
              <p className="mt-1 truncate font-mono text-[11px] text-muted">{t.evidence}</p>
            </div>
          );
        })}
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-3 gap-3">
        <MetricChart title="FIRST RECOMMENDATION CORRECT" unit="by times this class had been seen" data={points(report, "accuracy")} fmt={pct} />
        <MetricChart title="MTTR (SIM MINUTES)" unit="lower is better" data={points(report, "mttr_min")} fmt={(v) => v.toFixed(1)} />
        <MetricChart title="TOOL CALLS" unit="all attempts, lower is better" data={points(report, "tool_calls")} fmt={(v) => v.toFixed(1)} />
      </div>

      <div className="grid grid-cols-3 gap-3 font-mono text-[12px]">
        <Fact label="False replays (memory ON)" value={`${s.discrimination.memory_on.probes.k} / ${s.discrimination.memory_on.probes.n} probes`} sub={`memory OFF: ${s.discrimination.memory_off.probes.k} / ${s.discrimination.memory_off.probes.n}`} />
        <Fact
          label="Retrieval recall@1"
          value={s.retrieval.recall_at_1 ? `${pct(s.retrieval.recall_at_1.mean)} (n=${s.retrieval.recall_at_1.n})` : "—"}
          sub={s.retrieval.strong_precision ? `"strong" matches of the same class: ${pct(s.retrieval.strong_precision.mean)}` : ""}
        />
        <Fact
          label="Confidence honesty (Brier, lower is better)"
          value={`ON ${s.calibration.memory_on.brier?.toFixed(3) ?? "—"} · OFF ${s.calibration.memory_off.brier?.toFixed(3) ?? "—"}`}
          sub="0 = perfect · 0.25 = always saying 50%"
        />
      </div>
    </div>
  );
}

function Fact({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="rounded-md border border-border bg-panel px-3 py-2">
      <div className="text-[10px] uppercase tracking-widest text-muted">{label}</div>
      <div className="text-sm text-text">{value}</div>
      <div className="truncate text-[11px] text-muted">{sub}</div>
    </div>
  );
}
