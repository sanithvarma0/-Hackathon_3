"use client";

import { useState } from "react";
import { Modal } from "@/components/TopBar";
import type { CustomSpec } from "@/lib/types";

const MACHINES = [
  { id: "M1", name: "CNC vertical mill" },
  { id: "M2", name: "CNC lathe" },
  { id: "M3", name: "Robotic welding cell" },
  { id: "M4", name: "5-axis machining center" },
  { id: "M5", name: "Material-handling cell" },
];

const DEFAULT: CustomSpec = {
  machine: "M3",
  config_changed: false,
  minutes_before: 10,
  throughput_delta: -30,
  error_rate: 4,
  temperature: "normal",
  calibration: "fresh",
  network: "normal",
  memory_trend: "flat",
};

/** Structured only — no free text reaches the simulator (BUILD_PLAN.md 10.6). */
export default function CustomBuilder({ onRun, onClose }: { onRun: (spec: CustomSpec) => void; onClose: () => void }) {
  const [spec, setSpec] = useState<CustomSpec>(DEFAULT);
  const set = <K extends keyof CustomSpec>(k: K, v: CustomSpec[K]) => setSpec((s) => ({ ...s, [k]: v }));

  // What the agent will be able to observe — never the class the simulator assigns.
  const signals = [
    `throughput ${spec.throughput_delta}% on ${spec.machine}`,
    `error rate ${spec.error_rate.toFixed(1)}%`,
    spec.config_changed && `config deploy ${spec.minutes_before} min before onset`,
    spec.temperature === "high" && "high temperature readings",
    spec.calibration === "old" && "sensor calibration 30+ days old, noisy readings",
    spec.network === "degraded" && "packet loss + latency on the machine's gateway",
    spec.memory_trend === "climbing" && "controller memory climbing",
  ].filter(Boolean) as string[];

  return (
    <Modal onClose={onClose} wide>
      <h3 className="font-mono text-sm font-semibold tracking-widest text-text">CUSTOM INCIDENT</h3>
      <p className="mt-1 text-sm text-muted">Pick the signals. The simulator decides what is actually wrong — and keeps it hidden until the incident closes.</p>

      <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-4 text-sm">
        <Field label="Machine">
          <select value={spec.machine} onChange={(e) => set("machine", e.target.value)} className="w-full rounded border border-border bg-panel-2 px-2 py-1.5 font-mono text-text">
            {MACHINES.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id} · {m.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Config changed before onset">
          <div className="flex items-center gap-3">
            <Segmented value={spec.config_changed ? "yes" : "no"} options={["no", "yes"]} onChange={(v) => set("config_changed", v === "yes")} />
            {spec.config_changed && (
              <label className="flex flex-1 items-center gap-2 font-mono text-xs text-muted">
                <input type="range" min={1} max={60} value={spec.minutes_before} onChange={(e) => set("minutes_before", Number(e.target.value))} className="flex-1 accent-[#58a6ff]" />
                {spec.minutes_before}m
              </label>
            )}
          </div>
        </Field>
        <Field label={`Throughput change · ${spec.throughput_delta}%`}>
          <input type="range" min={-50} max={-5} value={spec.throughput_delta} onChange={(e) => set("throughput_delta", Number(e.target.value))} className="w-full accent-[#58a6ff]" />
        </Field>
        <Field label={`Error rate · ${spec.error_rate.toFixed(1)}%`}>
          <input type="range" min={0} max={20} step={0.5} value={spec.error_rate} onChange={(e) => set("error_rate", Number(e.target.value))} className="w-full accent-[#58a6ff]" />
        </Field>
        <Field label="Temperature">
          <Segmented value={spec.temperature} options={["normal", "high"]} onChange={(v) => set("temperature", v as CustomSpec["temperature"])} />
        </Field>
        <Field label="Calibration">
          <Segmented value={spec.calibration} options={["fresh", "old"]} labels={{ old: "30+ days" }} onChange={(v) => set("calibration", v as CustomSpec["calibration"])} />
        </Field>
        <Field label="Network">
          <Segmented value={spec.network} options={["normal", "degraded"]} onChange={(v) => set("network", v as CustomSpec["network"])} />
        </Field>
        <Field label="Controller memory">
          <Segmented value={spec.memory_trend} options={["flat", "climbing"]} onChange={(v) => set("memory_trend", v as CustomSpec["memory_trend"])} />
        </Field>
      </div>

      <div className="mt-4 rounded border border-border bg-panel-2 px-3 py-2">
        <div className="font-mono text-[10px] tracking-widest text-muted">SIGNAL PREVIEW</div>
        <ul className="mt-1 font-mono text-xs text-text">
          {signals.map((s) => (
            <li key={s}>· {s}</li>
          ))}
        </ul>
      </div>

      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onClose} className="rounded border border-border px-3 py-1.5 font-mono text-xs text-text">
          Cancel
        </button>
        <button onClick={() => onRun(spec)} className="rounded bg-recovering px-4 py-1.5 font-mono text-xs font-bold text-[#0b0f14]">
          RUN
        </button>
      </div>
    </Modal>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 font-mono text-[11px] uppercase tracking-widest text-muted">{label}</div>
      {children}
    </div>
  );
}

function Segmented({
  value,
  options,
  labels,
  onChange,
}: {
  value: string;
  options: string[];
  labels?: Record<string, string>;
  onChange: (v: string) => void;
}) {
  return (
    <div className="inline-flex rounded border border-border p-0.5">
      {options.map((o) => (
        <button
          key={o}
          onClick={() => onChange(o)}
          className={`rounded px-3 py-1 font-mono text-xs ${value === o ? "bg-recovering/20 text-recovering" : "text-muted hover:text-text"}`}
        >
          {labels?.[o] ?? o}
        </button>
      ))}
    </div>
  );
}
