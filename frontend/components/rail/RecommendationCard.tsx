"use client";

import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import { fmtDuration, type IncidentRun } from "@/lib/reducer";
import { useNow } from "@/lib/useMemoryOps";
import { ACTIONS, type Action } from "@/lib/types";

const pretty = (a: string) => a.replace(/_/g, " ");

export default function RecommendationCard({
  run,
  simSpeed,
  onError,
}: {
  run: IncidentRun | null;
  simSpeed: number | null;
  onError: (title: string, body: string) => void;
}) {
  const [sending, setSending] = useState<string | null>(null);
  const verifying = run?.phase === "verifying" && !!run.verify && !run.verify.effect;
  const now = useNow(verifying);

  if (!run || run.phase === "detecting" || (run.phase === "investigating" && !run.recommendation)) {
    return (
      <section className="shrink-0 rounded-lg border border-border bg-panel px-3 py-3">
        <h2 className="font-mono text-xs font-semibold tracking-widest text-muted">RECOMMENDATION</h2>
        <p className="mt-1 text-sm text-muted">
          {!run
            ? "The agent recommends; you decide. Nothing runs without your approval."
            : run.phase === "detecting"
              ? "Waiting for the alert to fire…"
              : `Investigating${run.attempt > 1 ? ` (attempt ${run.attempt})` : ""}…`}
        </p>
      </section>
    );
  }

  const rec = run.recommendation;
  const memoryAccent = run.memoryEnabled ? "var(--color-memory)" : "var(--color-memory-off)";

  async function send(kind: "act" | "ignore", action?: Action) {
    if (!run) return;
    setSending(action ?? "IGNORE");
    try {
      if (kind === "act" && action) await api.act(run.id, action);
      else await api.ignore(run.id);
    } catch (e) {
      const err = e as ApiError;
      onError(err.code ?? "Request failed", err.message);
    } finally {
      setSending(null);
    }
  }

  // ---- outcome ----
  if (run.phase === "outcome" && run.outcome) {
    const o = run.outcome;
    const m = run.metrics;
    return (
      <section
        className="slide-in shrink-0 rounded-lg border bg-panel px-3 py-3"
        style={{ borderColor: o.resolved ? "var(--color-healthy)" : "var(--color-degraded)" }}
      >
        <div className="flex items-baseline justify-between">
          <h2 className="font-mono text-sm font-semibold" style={{ color: o.resolved ? "var(--color-healthy)" : "var(--color-degraded)" }}>
            {o.resolved ? "✓ RESOLVED" : "▲ ESCALATED TO ON-CALL"}
          </h2>
          <span className="font-mono text-xs text-muted">
            {run.id}
            {run.traceUrl && (
              <a href={run.traceUrl} target="_blank" rel="noreferrer" className="ml-2 text-recovering hover:underline" title="Full trace in Langfuse">
                trace ↗
              </a>
            )}
          </span>
        </div>
        <dl className="mt-2 grid grid-cols-3 gap-2 font-mono">
          <Stat label="MTTR (sim)" value={fmtDuration(o.mttr_sim_s)} />
          <Stat label="tool calls" value={String(run.toolCalls)} />
          <Stat label="attempts" value={String(Math.max(1, run.attempt))} />
        </dl>
        {run.view?.type && (
          <p className="mt-2 text-[13px] text-muted">
            Actual class: <span className="font-mono text-text">{pretty(run.view.type)}</span>
            {m && (
              <>
                {" · "}first recommendation{" "}
                <span style={{ color: m.recommendation_correct ? "var(--color-healthy)" : "var(--color-critical)" }}>
                  {m.recommendation_correct ? "correct" : "wrong"}
                </span>
              </>
            )}
          </p>
        )}
        {run.view?.engineer_note && <p className="mt-1 text-[13px] text-muted">Engineer: {run.view.engineer_note}</p>}
        <p className="mt-2 text-[13px]" style={{ color: memoryAccent }}>
          {run.memoryEnabled
            ? "◆ Episode retained — the next similar incident will recall it."
            : "◆ Memory wasn't used for this incident, but the episode was still retained for next time."}
        </p>
      </section>
    );
  }

  if (!rec) return null;
  const conf = Math.round((rec.calibrated_confidence ?? rec.confidence) * 100);
  const awaiting = run.phase === "awaiting";
  const options = (run.options.length ? run.options : [...ACTIONS]) as Action[];
  const recommended = run.recommended ?? rec.action;
  const others = options.filter((a) => a !== recommended && a !== "ESCALATE_HUMAN");

  // ---- verifying ----
  let verifyBlock: React.ReactNode = null;
  if (run.phase === "verifying" && run.verify) {
    const v = run.verify;
    const realWindow = simSpeed ? v.windowSimS / simSpeed : v.windowSimS / 10;
    const elapsed = Math.max(0, now / 1000 - v.startedTs);
    const pct = v.effect ? 100 : Math.min(100, (elapsed / realWindow) * 100);
    const simLeft = Math.max(0, Math.round(v.windowSimS * (1 - pct / 100)));
    verifyBlock = (
      <div className="mt-2">
        <div className="flex justify-between font-mono text-xs">
          <span className="text-recovering">
            {v.effect ? `↻ ${pretty(v.effect)}` : <><span className="spin">↻</span> verifying {pretty(v.action)}…</>}
          </span>
          <span className="text-muted">{v.effect ? `peak ${v.peakPct ?? "?"}%` : `${simLeft}s sim left`}</span>
        </div>
        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-[#1f2732]">
          <div className="h-full rounded-full bg-recovering transition-all duration-300" style={{ width: `${pct}%` }} />
        </div>
      </div>
    );
  }

  return (
    <section className="slide-in shrink-0 rounded-lg border border-recovering/50 bg-panel px-3 py-3">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="font-mono text-xs font-semibold tracking-widest text-muted">
          RECOMMENDATION{run.attempt > 1 ? ` · ATTEMPT ${run.attempt}` : ""}
        </h2>
        <span className="font-mono text-xs text-muted">{run.id}</span>
      </div>
      <div className="mt-1 flex items-baseline justify-between gap-3">
        <span className="font-mono text-lg font-bold text-text">{pretty(rec.action)}</span>
        <span className="font-mono text-sm text-muted">{conf}% confident</span>
      </div>
      <div className="mt-1 h-1 overflow-hidden rounded-full bg-[#1f2732]">
        <div className="h-full rounded-full bg-recovering" style={{ width: `${conf}%` }} />
      </div>
      <p className="mt-2 text-[13px] font-semibold leading-5 text-text">{rec.diagnosis}</p>
      <p className="line-clamp-3 text-[13px] leading-5 text-muted [@media(max-height:960px)]:line-clamp-2" title={rec.reasoning}>
        {rec.reasoning}
      </p>

      {(rec.cited_incidents.length > 0 || rec.actions_known_to_fail.length > 0 || run.failedActions.length > 0) && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {rec.cited_incidents.map((id) => (
            <span key={id} className="rounded border px-1.5 py-0.5 font-mono text-xs" style={{ borderColor: memoryAccent, color: memoryAccent }}>
              ◆ cites {id}
            </span>
          ))}
          {/* Purple only for what memory knew; a fix that failed in this incident is amber. */}
          {rec.actions_known_to_fail
            .filter((a) => !run.failedActions.includes(a))
            .map((a) => (
              <span
                key={a}
                className="rounded border px-1.5 py-0.5 font-mono text-xs"
                style={{ borderColor: memoryAccent, color: memoryAccent }}
                title="A past incident recorded this fix as ineffective"
              >
                ⚠ {pretty(a)} known to fail
              </span>
            ))}
          {run.failedActions.map((a) => (
            <span key={a} className="rounded border border-degraded px-1.5 py-0.5 font-mono text-xs text-degraded">
              ✖ {pretty(a)} failed this incident
            </span>
          ))}
        </div>
      )}

      {verifyBlock}

      {run.phase !== "verifying" && (
        <div className="mt-3 flex flex-col gap-2">
          <button
            disabled={!awaiting || !!sending}
            onClick={() => send("act", recommended)}
            className="rounded-md bg-recovering px-3 py-2 font-mono text-sm font-bold text-[#0b0f14] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {sending === recommended ? "Sending…" : `APPROVE · ${pretty(recommended)}`}
          </button>
          <div className="flex flex-wrap gap-1.5">
            {others.map((a) => (
              <button
                key={a}
                disabled={!awaiting || !!sending}
                onClick={() => send("act", a)}
                className="rounded border border-border px-2 py-1 font-mono text-xs text-text transition hover:border-recovering disabled:cursor-not-allowed disabled:opacity-40"
                title="Override the recommendation"
              >
                {pretty(a)}
              </button>
            ))}
          </div>
          <div className="flex gap-1.5 border-t border-border pt-2">
            <button
              disabled={!awaiting || !!sending}
              onClick={() => send("ignore")}
              className="flex-1 rounded border border-degraded/60 px-2 py-1 font-mono text-xs text-degraded transition hover:bg-degraded/10 disabled:cursor-not-allowed disabled:opacity-40"
              title="Do nothing: the condition keeps getting worse"
            >
              IGNORE
            </button>
            <button
              disabled={!awaiting || !!sending || recommended === "ESCALATE_HUMAN"}
              onClick={() => send("act", "ESCALATE_HUMAN")}
              className="flex-1 rounded border border-border px-2 py-1 font-mono text-xs text-muted transition hover:text-text disabled:cursor-not-allowed disabled:opacity-40"
            >
              ESCALATE TO HUMAN
            </button>
          </div>
          {!awaiting && <p className="font-mono text-xs text-muted">Preparing the decision…</p>}
        </div>
      )}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-panel-2 px-2 py-1">
      <dt className="text-[10px] uppercase tracking-widest text-muted">{label}</dt>
      <dd className="text-base font-semibold text-text">{value}</dd>
    </div>
  );
}
