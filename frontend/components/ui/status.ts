import type { MachineStatus } from "@/lib/types";

// Never colour alone: every state also has an icon and a label (BUILD_PLAN.md 10.3).
export const STATUS: Record<MachineStatus, { color: string; icon: string; label: string }> = {
  healthy: { color: "#3fb950", icon: "●", label: "HEALTHY" },
  degraded: { color: "#d29922", icon: "▲", label: "DEGRADED" },
  critical: { color: "#f85149", icon: "✖", label: "CRITICAL" },
  recovering: { color: "#58a6ff", icon: "↻", label: "RECOVERING" },
};

// Thresholds from the simulator's own ranges (backend/simulator/incidents.py, engine.py):
// baseline temperature 36–43 °C, phantom drift adds 28–45 °C; sensor variance ~0.03 normal,
// 0.2–0.4 when drifting; memory climbs to 96% under exhaustion.
export const TEMP_ALARM_C = 65;
export const JITTER_VARIANCE = 0.1;
export const MEMORY_WARN_PCT = 70;
export const OOM_PCT = 90;
