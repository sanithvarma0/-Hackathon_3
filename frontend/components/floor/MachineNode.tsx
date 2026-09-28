import type { Point } from "@/lib/reducer";
import type { MachineMetrics } from "@/lib/types";
import {
  JITTER_VARIANCE,
  MEMORY_WARN_PCT,
  OOM_PCT,
  STATUS,
  TEMP_ALARM_C,
} from "@/components/ui/status";

const SPARK_W = 146;
const SPARK_H = 46;
const FLOOR_PCT = 40; // fixed axis 40–100%: a dip always looks as deep as it is

function Sparkline({
  points,
  ticks,
  color,
  jitter,
}: {
  points: Point[];
  ticks: number[];
  color: string;
  jitter: boolean;
}) {
  if (points.length < 2) {
    return <div style={{ height: SPARK_H }} className="font-mono text-[10px] text-muted">collecting…</div>;
  }
  const t0 = points[0].ts;
  const t1 = points[points.length - 1].ts;
  const x = (ts: number) => ((ts - t0) / Math.max(1, t1 - t0)) * SPARK_W;
  const y = (v: number) => SPARK_H - ((Math.max(FLOOR_PCT, v) - FLOOR_PCT) / (100 - FLOOR_PCT)) * SPARK_H;
  const line = points.map((p) => `${x(p.ts).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  return (
    <svg width={SPARK_W} height={SPARK_H} className="overflow-visible" aria-label="throughput, last 10 sim-minutes">
      <line x1={0} x2={SPARK_W} y1={y(90)} y2={y(90)} stroke="#2a3441" strokeDasharray="2 3" />
      <defs>
        <linearGradient id={`fade-${color.slice(1)}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.28} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <polygon points={`0,${SPARK_H} ${line} ${SPARK_W},${SPARK_H}`} fill={`url(#fade-${color.slice(1)})`} />
      <polyline points={line} fill="none" stroke={color} strokeWidth={1.6} className={jitter ? "jitter" : undefined} />
      {ticks
        .filter((ts) => ts >= t0 && ts <= t1)
        .map((ts) => (
          <g key={ts}>
            <line x1={x(ts)} x2={x(ts)} y1={-2} y2={SPARK_H} stroke="#e6edf3" strokeOpacity={0.7} strokeDasharray="2 2" />
            <text x={x(ts) + 2} y={6} fontSize={9} fill="#e6edf3" className="font-mono">
              cfg
            </text>
          </g>
        ))}
    </svg>
  );
}

function Badge({ children, color, flicker }: { children: React.ReactNode; color: string; flicker?: boolean }) {
  return (
    <span
      className={`rounded px-1 py-px font-mono text-[10px] font-semibold leading-none ${flicker ? "flicker" : ""}`}
      style={{ color, border: `1px solid ${color}` }}
    >
      {children}
    </span>
  );
}

export default function MachineNode({
  m,
  spark,
  configTicks,
  focused,
  incidentHere,
  flashKey,
  starved,
}: {
  m: MachineMetrics;
  spark: Point[];
  configTicks: number[];
  focused: boolean;
  incidentHere: boolean;
  flashKey: number | null;
  starved: boolean;
}) {
  const st = STATUS[m.status];
  const memColor =
    m.memory_pct >= OOM_PCT ? STATUS.critical.color : m.memory_pct >= MEMORY_WARN_PCT ? STATUS.degraded.color : "#6e7f93";
  const tempAlarm = m.temperature_c >= TEMP_ALARM_C;
  const jitter = m.sensor_variance > JITTER_VARIANCE;
  const shortName = m.name.replace(/^Robotic /, "").replace(/ cell$/, "");

  return (
    <div
      key={flashKey ?? "steady"}
      className={[
        "relative flex h-full w-full flex-col gap-1 rounded-lg border bg-panel px-3 py-2 transition-colors",
        m.status === "critical" ? "pulse-critical" : "",
        focused ? "focus-ring" : "",
        flashKey ? "flash-red" : "",
      ].join(" ")}
      style={{ borderColor: m.status === "healthy" && !incidentHere ? "var(--color-border)" : st.color }}
      title={`${m.name} — ${m.profile}\nconfig ${m.config_version} · calibrated ${m.calibration_age_days.toFixed(0)} d ago\nOEE ${m.oee_pct}% · errors ${m.error_rate_pct}% · ${m.temperature_c} °C · loss ${m.packet_loss_pct}%`}
    >
      <div className="flex items-center justify-between">
        <span className="font-mono text-base font-bold text-text">{m.machine_id}</span>
        <span
          className="flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold tracking-wider"
          style={{ color: st.color, background: `${st.color}1f` }}
        >
          <span className={m.status === "recovering" ? "spin" : undefined}>{st.icon}</span>
          {st.label}
        </span>
      </div>
      <div className="-mt-1 truncate text-[11px] text-muted">{shortName}</div>

      <div className="flex items-baseline justify-between">
        <span className="font-mono text-xl font-semibold" style={{ color: m.status === "healthy" ? "var(--color-text)" : st.color }}>
          {m.throughput_pct.toFixed(1)}
          <span className="text-xs text-muted">%</span>
        </span>
        <span className="font-mono text-[10px] text-muted">{starved ? "STARVED" : "throughput"}</span>
      </div>

      <Sparkline points={spark} ticks={configTicks} color={st.color} jitter={jitter} />

      <div className="mt-auto flex items-center gap-1.5">
        <div className="flex flex-1 items-center gap-1" title={`controller memory ${m.memory_pct}%`}>
          <span className="font-mono text-[10px] text-muted">mem</span>
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[#1f2732]">
            <div className="h-full rounded-full transition-all duration-700" style={{ width: `${m.memory_pct}%`, background: memColor }} />
          </div>
        </div>
        {m.memory_pct >= OOM_PCT && <Badge color={STATUS.critical.color}>OOM</Badge>}
        {tempAlarm && (
          <Badge color={STATUS.critical.color} flicker={jitter}>
            {m.temperature_c.toFixed(0)}°C
          </Badge>
        )}
        {m.packet_loss_pct > 2 && <Badge color={STATUS.degraded.color}>NET</Badge>}
      </div>
    </div>
  );
}
