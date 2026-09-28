"use client";

import { useEffect, useRef, useState } from "react";
import type { IncidentRun, Point } from "@/lib/reducer";
import type { GatewayState, MachineMetrics, PlantState } from "@/lib/types";
import MachineNode from "@/components/floor/MachineNode";
import { STATUS } from "@/components/ui/status";
import { useNow } from "@/lib/useMemoryOps";

// Fixed design space; the whole floor scales to fit its box (1920×1080 and 1440×900).
const W = 1200;
const H = 580;
const NODE_W = 170;
const NODE_H = 164;

type Box = { x: number; y: number; w: number; h: number };

// Topology (BUILD_PLAN.md 5.2): FEED → M1 → M2 → M3 → M4 → SHIP; M5 loads M2 and M4.
const MACHINE_BOX: Record<string, Box> = {
  M1: { x: 150, y: 140, w: NODE_W, h: NODE_H },
  M2: { x: 390, y: 140, w: NODE_W, h: NODE_H },
  M3: { x: 630, y: 140, w: NODE_W, h: NODE_H },
  M4: { x: 870, y: 140, w: NODE_W, h: NODE_H },
  M5: { x: 630, y: 396, w: NODE_W, h: NODE_H },
};
const FEED: Box = { x: 30, y: 197, w: 84, h: 50 };
const SHIP: Box = { x: 1090, y: 197, w: 84, h: 50 };
const GATEWAY_BOX: Record<string, Box> = {
  "GW-A": { x: 240, y: 470, w: 150, h: 54 },
  "GW-B": { x: 790, y: 26, w: 150, h: 54 },
};
const LINE_Y = 222;

const cx = (b: Box) => b.x + b.w / 2;

/** Gateway → machine link endpoints, routed so no link crosses a node. */
function gatewayLink(gw: string, m: string): [number, number, number, number] {
  const g = GATEWAY_BOX[gw];
  const b = MACHINE_BOX[m];
  if (gw === "GW-B") return [cx(g) + (m === "M3" ? -30 : 30), g.y + g.h, cx(b), b.y];
  if (m === "M5") return [g.x + g.w, g.y + g.h / 2, b.x, b.y + b.h / 2 + 20];
  if (m === "M1") return [cx(g) - 30, g.y, cx(b), b.y + b.h];
  return [cx(g) + 30, g.y, b.x + 40, b.y + b.h]; // M2
}

function flowClass(pct: number): string {
  if (pct < 45) return "flow flow-stalled";
  if (pct < 88) return "flow flow-slow";
  return "flow";
}

function useFit(ref: React.RefObject<HTMLDivElement | null>) {
  const [scale, setScale] = useState(1);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setScale(Math.max(0.3, Math.min(width / W, height / H)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return scale;
}

export default function PlantFloor({
  plant,
  spark,
  configTicks,
  incident,
  memoryOn,
}: {
  plant: PlantState | null;
  spark: Record<string, Point[]>;
  configTicks: Record<string, number[]>;
  incident: IncidentRun | null;
  memoryOn: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const scale = useFit(ref);
  const nowS = useNow(!!incident?.redegradedTs, 1000) / 1000;

  if (!plant) {
    return (
      <div ref={ref} className="flex h-full items-center justify-center font-mono text-sm text-muted">
        Connecting to the plant…
      </div>
    );
  }

  const byId = Object.fromEntries(plant.machines.map((m) => [m.machine_id, m])) as Record<string, MachineMetrics>;
  const gateways = Object.fromEntries(plant.gateways.map((g) => [g.gateway_id, g])) as Record<string, GatewayState>;
  const tp = (id: string) => byId[id]?.throughput_pct ?? 100;

  // Material reaching each link: a stage passes at most what it receives (5.2).
  const flowInto: number[] = [];
  let upstream = 100;
  for (const id of ["M1", "M2", "M3", "M4"]) {
    let inbound = upstream;
    if (id === "M2" || id === "M4") inbound = Math.min(inbound, tp("M5"));
    flowInto.push(inbound);
    upstream = Math.min(inbound, tp(id));
  }
  flowInto.push(upstream); // into SHIP
  const stations = [FEED, MACHINE_BOX.M1, MACHINE_BOX.M2, MACHINE_BOX.M3, MACHINE_BOX.M4, SHIP];

  const active = incident && incident.phase !== "outcome" ? incident : null;

  return (
    <div
      ref={ref}
      className="relative h-full w-full overflow-hidden rounded-lg"
      style={{
        backgroundImage: "linear-gradient(#141b24 1px, transparent 1px), linear-gradient(90deg, #141b24 1px, transparent 1px)",
        backgroundSize: `${40 * scale}px ${40 * scale}px`,
        backgroundPosition: "center",
      }}
    >
      <div
        className="absolute left-1/2 top-1/2"
        style={{ width: W, height: H, transform: `translate(-50%, -50%) scale(${scale})` }}
      >
        {/* floor grid */}
        <svg width={W} height={H} className="absolute inset-0" aria-hidden>
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0,0 L10,5 L0,10 z" fill="#3a4556" />
            </marker>
          </defs>

          {/* gateway (network) links */}
          {Object.entries(GATEWAY_BOX).flatMap(([gw]) =>
            (gateways[gw]?.machines ?? []).map((m) => {
              const [x1, y1, x2, y2] = gatewayLink(gw, m);
              const status = gateways[gw]?.status ?? "healthy";
              const color = status === "healthy" ? "#2a3441" : STATUS[status].color;
              return (
                <line
                  key={`${gw}-${m}`}
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  stroke={color}
                  strokeWidth={status === "healthy" ? 1.5 : 2.5}
                  strokeDasharray="3 6"
                  className={status === "critical" ? "flicker" : undefined}
                />
              );
            }),
          )}

          {/* production line */}
          {stations.slice(0, -1).map((from, i) => {
            const to = stations[i + 1];
            const x1 = from.x + from.w;
            const x2 = to.x;
            const pct = flowInto[i];
            const starved = pct < 88;
            return (
              <g key={i}>
                <line x1={x1} y1={LINE_Y} x2={x2} y2={LINE_Y} stroke="#2a3441" strokeWidth={6} strokeLinecap="round" />
                <line
                  x1={x1}
                  y1={LINE_Y}
                  x2={x2 - 4}
                  y2={LINE_Y}
                  stroke={starved ? STATUS.degraded.color : "#9fb0c3"}
                  strokeOpacity={pct < 45 ? 0.35 : starved ? 0.8 : 0.9}
                  strokeWidth={3}
                  strokeLinecap="round"
                  className={flowClass(pct)}
                  markerEnd="url(#arrow)"
                />
              </g>
            );
          })}

          {/* M5 loads M2 and M4 */}
          {(["M2", "M4"] as const).map((target) => {
            const m5 = MACHINE_BOX.M5;
            const b = MACHINE_BOX[target];
            const fromX = target === "M2" ? m5.x + 30 : m5.x + m5.w - 30;
            const toX = target === "M2" ? b.x + b.w - 40 : b.x + 40;
            const d = `M ${fromX} ${m5.y} C ${fromX} ${m5.y - 50}, ${toX} ${b.y + b.h + 50}, ${toX} ${b.y + b.h + 4}`;
            const pct = tp("M5");
            return (
              <g key={target}>
                <path d={d} fill="none" stroke="#2a3441" strokeWidth={5} />
                <path
                  d={d}
                  fill="none"
                  stroke={pct < 88 ? STATUS.degraded.color : "#9fb0c3"}
                  strokeOpacity={0.75}
                  strokeWidth={2.5}
                  className={flowClass(pct)}
                  markerEnd="url(#arrow)"
                />
              </g>
            );
          })}
        </svg>

        {/* feed + ship */}
        {[
          { box: FEED, label: "FEED", sub: "raw stock" },
          { box: SHIP, label: "SHIP", sub: `${plant.line_throughput_pct.toFixed(0)}% out` },
        ].map(({ box, label, sub }) => (
          <div
            key={label}
            className="absolute flex flex-col items-center justify-center rounded-md border border-border bg-panel font-mono"
            style={{ left: box.x, top: box.y, width: box.w, height: box.h }}
          >
            <span className="text-sm font-semibold tracking-widest text-text">{label}</span>
            <span className="text-[11px] text-muted">{sub}</span>
          </div>
        ))}

        {/* gateways */}
        {Object.entries(GATEWAY_BOX).map(([gw, box]) => {
          const g = gateways[gw];
          const st = STATUS[g?.status ?? "healthy"];
          return (
            <div
              key={gw}
              className={`absolute flex items-center gap-3 rounded-md border bg-panel px-3 font-mono ${g?.status === "critical" ? "pulse-critical" : ""}`}
              style={{ left: box.x, top: box.y, width: box.w, height: box.h, borderColor: g?.status === "healthy" ? "var(--color-border)" : st.color }}
              title={`${gw} serves ${(g?.machines ?? []).join(", ")}`}
            >
              <span style={{ color: st.color }} className="text-base">
                {st.icon}
              </span>
              <div className="leading-tight">
                <div className="text-sm font-semibold text-text">{gw}</div>
                <div className="text-[11px] text-muted">
                  loss <span style={{ color: g?.status === "healthy" ? undefined : st.color }}>{g?.packet_loss_pct.toFixed(1) ?? "—"}%</span>
                </div>
              </div>
            </div>
          );
        })}

        {/* machines */}
        {Object.entries(MACHINE_BOX).map(([id, box]) => {
          const m = byId[id];
          if (!m) return null;
          const involved = !!active?.view && (active.view.machine_id === id || active.view.affected.includes(id));
          const flashing =
            !!incident?.redegradedTs && involved && nowS - incident.redegradedTs < 6 ? incident.redegradedTs : null;
          return (
            <div key={id} className="absolute" style={{ left: box.x, top: box.y, width: box.w, height: box.h }}>
              <MachineNode
                m={m}
                spark={spark[id] ?? []}
                configTicks={configTicks[id] ?? []}
                focused={active?.focusMachine === id && active.phase === "investigating"}
                incidentHere={involved}
                flashKey={flashing}
                starved={plant.starved.includes(id)}
              />
            </div>
          );
        })}

        {/* legend */}
        <div className="absolute bottom-2 right-3 flex items-center gap-4 font-mono text-[11px] text-muted">
          {(["healthy", "degraded", "critical", "recovering"] as const).map((s) => (
            <span key={s} className="flex items-center gap-1">
              <span style={{ color: STATUS[s].color }}>{STATUS[s].icon}</span>
              {STATUS[s].label.toLowerCase()}
            </span>
          ))}
          <span className="flex items-center gap-1">
            <span className="inline-block h-2.5 w-2.5 rounded-sm ring-2 ring-recovering" /> agent looking
          </span>
          <span className="flex items-center gap-1" style={{ color: memoryOn ? "var(--color-memory)" : "var(--color-memory-off)" }}>
            ■ memory
          </span>
        </div>
      </div>
    </div>
  );
}
