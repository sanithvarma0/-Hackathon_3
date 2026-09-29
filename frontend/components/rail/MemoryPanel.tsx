import type { IncidentRun } from "@/lib/reducer";

/** Everything here comes from Hindsight, so everything here is purple (BUILD_PLAN.md 10.1). */
export default function MemoryPanel({
  run,
  memoryOn,
  onOpenIncident,
}: {
  run: IncidentRun | null;
  memoryOn: boolean;
  onOpenIncident: (id: string) => void;
}) {
  const enabled = run ? run.memoryEnabled : memoryOn;
  const accent = enabled ? "var(--color-memory)" : "var(--color-memory-off)";

  let body: React.ReactNode;
  if (!enabled) {
    body = <p className="text-muted">Memory disabled — the agent reasons from evidence only.</p>;
  } else if (!run) {
    body = (
      <p className="text-muted">
        Consulted twice per incident: <span style={{ color: accent }}>hints</span> before investigating, and a{" "}
        <span style={{ color: accent }}>signature match</span> before recommending.
      </p>
    );
  } else {
    const nothingYet = !run.hintsQuery && !run.matches.length && !run.learnedPatterns.length;
    body = (
      <div className="flex flex-col gap-2">
        {nothingYet && <p className="text-muted">Waiting for the alert…</p>}
        {run.hintsQuery !== null && (
          <div>
            <Label>Hints</Label>
            {run.hints.length ? (
              <ul className="space-y-0.5">
                {run.hints.slice(0, 3).map((h, i) => (
                  <li key={i} className="line-clamp-2 text-[13px] leading-5 [@media(max-height:960px)]:line-clamp-1" style={{ color: accent }} title={h}>
                    ◆ {h}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-muted">No similar past incidents yet — first time seeing this.</p>
            )}
          </div>
        )}
        {run.searched && (
          <div>
            <Label>Matched incidents</Label>
            {run.matches.length ? (
              <div className="flex flex-wrap gap-1.5">
                {run.matches.map((m) => (
                  <button
                    key={m.incident_id}
                    onClick={() => onOpenIncident(m.incident_id)}
                    className="memory-in rounded border px-2 py-0.5 font-mono text-[13px] hover:bg-[#a371f71a]"
                    style={{ borderColor: accent, color: accent }}
                    title={[
                      `${m.incident_id} — ${m.diagnosis ?? "?"}`,
                      `fix: ${m.final_action ?? "?"} → ${m.outcome ?? "?"}`,
                      `reranker ${m.rerank.toFixed(3)}${m.similarity != null ? ` · semantic ${m.similarity.toFixed(3)}` : ""}`,
                      "click to open in the Memory Browser",
                    ].join("\n")}
                  >
                    {m.incident_id} · #{m.rank} · {m.strength}
                  </button>
                ))}
              </div>
            ) : (
              <p className="text-[13px] text-muted">No past incident shares this signature.</p>
            )}
          </div>
        )}
        {run.memoryGate && run.memoryGate !== "reranker" && (
          <p className="font-mono text-[11px] text-degraded" title="Hindsight returned no cross-encoder scores (passthrough reranker)">
            ▲ Hindsight reranker unavailable — {run.memoryGate === "llm_judge" ? "matches checked by the LLM" : "matches in rank order only"}
          </p>
        )}
        {run.learnedPatterns.length > 0 && (
          <div>
            <Label>Learned patterns</Label>
            <ul className="space-y-0.5">
              {run.learnedPatterns.slice(0, 2).map((p, i) => (
                <li key={i} className="line-clamp-2 text-[13px] leading-5" style={{ color: accent }}>
                  ◆ {p}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }

  return (
    <section
      className="flex min-h-[88px] shrink flex-col rounded-lg border bg-panel"
      style={{ borderColor: enabled ? "#a371f755" : "var(--color-border)" }}
    >
      <header className="flex items-center justify-between border-b px-3 py-2" style={{ borderColor: enabled ? "#a371f733" : "var(--color-border)" }}>
        <h2 className="font-mono text-xs font-semibold tracking-widest" style={{ color: accent }}>
          ◆ MEMORY {enabled ? "" : "· OFF"}
        </h2>
        <span className="font-mono text-[11px] text-muted">Hindsight</span>
      </header>
      <div className="scroll-thin min-h-0 max-h-[30vh] overflow-y-auto px-3 py-2 text-sm">{body}</div>
    </section>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <div className="mb-0.5 font-mono text-[11px] uppercase tracking-widest text-muted">{children}</div>;
}
