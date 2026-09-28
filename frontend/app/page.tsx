"use client";

import { useCallback, useEffect, useState } from "react";
import BottomBar, { type Tab } from "@/components/BottomBar";
import CustomBuilder from "@/components/CustomBuilder";
import PlantFloor from "@/components/floor/PlantFloor";
import MemoryPanel from "@/components/rail/MemoryPanel";
import RecommendationCard from "@/components/rail/RecommendationCard";
import TracePanel from "@/components/rail/TracePanel";
import Learning from "@/components/tabs/Learning";
import MemoryBrowser from "@/components/tabs/MemoryBrowser";
import Toasts from "@/components/Toasts";
import TopBar from "@/components/TopBar";
import { API_BASE, ApiError, api } from "@/lib/api";
import { humanCode } from "@/lib/reducer";
import { useMemoryOps } from "@/lib/useMemoryOps";
import type { CustomSpec } from "@/lib/types";

const MEMORY_KEY = "memoryops.memory_enabled";

export default function Home() {
  const [state, dispatch] = useMemoryOps();
  const [tab, setTab] = useState<Tab>("floor");
  const [memoryOn, setMemoryOnState] = useState(true);
  const [customOpen, setCustomOpen] = useState(false);
  const [memoryFocus, setMemoryFocus] = useState<string | null>(null);

  useEffect(() => {
    const saved = window.localStorage.getItem(MEMORY_KEY);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- restore a saved preference once
    if (saved !== null) setMemoryOnState(saved === "true");
  }, []);
  const setMemoryOn = (on: boolean) => {
    setMemoryOnState(on);
    window.localStorage.setItem(MEMORY_KEY, String(on));
  };

  const toastError = useCallback(
    (title: string, body: string) => dispatch({ type: "toast", toast: { tone: "critical", title: humanCode(title), body } }),
    [dispatch],
  );
  const dismiss = useCallback((id: number) => dispatch({ type: "dismiss_toast", id }), [dispatch]);

  /** Refusals the backend owns arrive on the stream as `error` events; only toast what can't. */
  const guard = async (request: () => Promise<unknown>) => {
    try {
      await request();
      setTab("floor");
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 0 || err.status === 422) toastError(err.code, err.message);
    }
  };

  const plant = state.plant;
  const active = plant?.active_incident_id ?? null;
  const run = state.incident;

  return (
    <div className="flex h-screen min-h-0 flex-col overflow-hidden">
      <TopBar
        plant={plant}
        connection={state.connection}
        bankId={state.bankId}
        simSpeed={state.simSpeed}
        memoryOn={memoryOn}
        setMemoryOn={setMemoryOn}
        usageVersion={state.usageVersion}
        incidentActive={!!active}
        onReset={(wipe) => guard(() => api.reset("live", wipe))}
      />

      <main className="relative min-h-0 flex-1">
        {tab === "floor" && (
          <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_minmax(420px,30%)]">
            <div className="relative min-h-0 p-3">
              <PlantFloor plant={plant} spark={state.spark} configTicks={state.configTicks} incident={run} memoryOn={memoryOn} />
              {run?.view && run.phase !== "outcome" && (
                <div className="absolute left-5 top-5 rounded-md border border-critical/60 bg-panel/90 px-3 py-2 font-mono text-xs">
                  <span className="text-critical">● {run.id}</span>
                  <span className="ml-2 text-muted">
                    {run.view.machine_id}
                    {run.view.affected.length > 1 ? ` + ${run.view.affected.length - 1} more` : ""} ·{" "}
                    {run.view.type ? run.view.type.replace(/_/g, " ") : "class hidden until closed"} · memory{" "}
                    <span style={{ color: run.memoryEnabled ? "var(--color-memory)" : "var(--color-memory-off)" }}>
                      {run.memoryEnabled ? "ON" : "OFF"}
                    </span>
                  </span>
                </div>
              )}
            </div>
            <aside className="scroll-thin flex min-h-0 flex-col gap-3 overflow-y-auto border-l border-border p-3">
              <TracePanel run={run} memoryOn={memoryOn} />
              <MemoryPanel
                run={run}
                memoryOn={memoryOn}
                onOpenIncident={(id) => {
                  setMemoryFocus(id);
                  setTab("memory");
                }}
              />
              <RecommendationCard run={run} simSpeed={state.simSpeed} onError={toastError} />
            </aside>
          </div>
        )}
        {tab === "memory" && <MemoryBrowser version={state.memoryVersion} focusId={memoryFocus} runbookLive={state.runbook} />}
        {tab === "learning" && <Learning version={state.metricsVersion} />}

        <Toasts toasts={state.toasts} dismiss={dismiss} />

        {state.connection === "offline" && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-bg/80">
            <div className="w-[460px] rounded-lg border border-border bg-panel p-5">
              <h2 className="font-mono text-sm font-semibold text-degraded">▲ Backend unreachable</h2>
              <p className="mt-2 text-sm text-muted">
                The control room can&apos;t reach <span className="font-mono text-text">{API_BASE}</span>. Retrying every 3 seconds — nothing
                is lost: the stream replays missed events when it reconnects.
              </p>
              <pre className="mt-3 rounded bg-bg px-3 py-2 font-mono text-xs text-text">make backend</pre>
            </div>
          </div>
        )}
      </main>

      <BottomBar
        busy={!!active}
        busyReason={`${active ?? "An incident"} is active — one incident at a time`}
        onPredefined={(type) => guard(() => api.triggerPredefined(type, memoryOn))}
        onRandom={() => guard(() => api.triggerRandom(memoryOn))}
        onCustom={() => setCustomOpen(true)}
        tab={tab}
        setTab={(t) => {
          if (t !== "memory") setMemoryFocus(null);
          setTab(t);
        }}
      />

      {customOpen && (
        <CustomBuilder
          onClose={() => setCustomOpen(false)}
          onRun={(spec: CustomSpec) => {
            setCustomOpen(false);
            void guard(() => api.triggerCustom(spec, memoryOn));
          }}
        />
      )}
    </div>
  );
}
