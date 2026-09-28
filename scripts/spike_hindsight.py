"""Phase 0.5 spike: measure how Hindsight behaves for our episode records.

Answers the plan's open questions (BUILD_PLAN.md Section 13, Phase 0.5):
  1. How long does a synchronous retain take?
  2. Is a memory recallable immediately after retain?
  3. What recall scores (semantic / reranker / final) do true vs false matches get?
     -> sets MEMORY_MATCH_THRESHOLD
  4. How long until observations (consolidated beliefs) show up?

Uses a throwaway bank that is deleted at the end (pass --keep to inspect it in the
Hindsight Cloud UI).

Run: uv run python scripts/spike_hindsight.py
"""

import argparse
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from hindsight_client import Hindsight

from backend.config import get_settings

# Two config regressions (different machines, different wording) and one sensor drift.
# INC-S01 and INC-S02 share a signature; INC-S03 is a decoy with a different root cause.
EPISODES = [
    {
        "incident_id": "INC-S01",
        "machine_id": "M3",
        "diagnosis": "config regression",
        "final_action": "ROLLBACK_CONFIG",
        "text": """INCIDENT INC-S01 — 2026-09-14 09:12
MACHINE: M3 (Robotic welding cell — Fanuc ARC Mate 100iD, commissioned 2022)

SYMPTOMS: throughput -38% over 6 min, onset gradual,
error logs: "servo timeout on axis J4"; "cycle time +31% (nominal exceeded)".

CONTEXT: config changed 11 min before onset (v2.14.3 -> v2.15.0); calibration age
9 days; network status normal; sensor variance 0.03.

DIAGNOSIS: config regression, confidence 0.62.

INVESTIGATION PATH: get_machine_metrics(M3) -> throughput 61%; get_error_logs(M3) ->
servo timeouts; get_recent_events(M3) -> config v2.15.0 deployed 11 min before onset.
DECISIVE EVIDENCE: recent events showed a config deploy shortly before a gradual decline
(7 tool calls; 1 was decisive).

ACTIONS ATTEMPTED:
1. ROLLBACK_CONFIG -> full_recovery (98%)

RESOLUTION: final action ROLLBACK_CONFIG, MTTR 9.5 sim-minutes.
OUTCOME: successful.
LESSON: A gradual throughput decline shortly after a config deploy is a config regression;
roll back the config.""",
    },
    {
        "incident_id": "INC-S02",
        "machine_id": "M4",
        "diagnosis": "config regression",
        "final_action": "ROLLBACK_CONFIG",
        "text": """INCIDENT INC-S02 — 2026-09-15 14:40
MACHINE: M4 (5-axis machining center — DMG Mori NVX 5080, commissioned 2020)

SYMPTOMS: throughput -29% over 5 min, onset gradual,
error logs: "control loop jitter, position error 0.12mm"; "feed override clamped at 70%".

CONTEXT: config changed 16 min before onset (v3.2.0 -> v3.3.1); calibration age 12 days;
network status normal; sensor variance 0.02.

DIAGNOSIS: config regression, confidence 0.71.

INVESTIGATION PATH: get_recent_events(M4) -> config v3.3.1 deployed 16 min before onset;
get_metric_history(M4, throughput, 1h) -> gradual decline.
DECISIVE EVIDENCE: config deploy shortly before a gradual decline (4 tool calls; 1 was decisive).

ACTIONS ATTEMPTED:
1. RESTART_MACHINE -> partial_recovery (71%, re-degraded after 95s)
2. ROLLBACK_CONFIG -> full_recovery (97%)

RESOLUTION: final action ROLLBACK_CONFIG, MTTR 14.0 sim-minutes.
OUTCOME: successful.
LESSON: Restarting only gives temporary relief for config-regression signatures; go
straight to rollback.""",
    },
    {
        "incident_id": "INC-S03",
        "machine_id": "M2",
        "diagnosis": "sensor drift",
        "final_action": "RECALIBRATE_SENSOR",
        "text": """INCIDENT INC-S03 — 2026-09-16 07:05
MACHINE: M2 (CNC lathe — Mazak QT-250, commissioned 2019)

SYMPTOMS: throughput -22% over 8 min, onset gradual,
error logs: "spindle temperature alarm 81C (phantom, IR reading 44C)"; "probe variance high".

CONTEXT: config unchanged for 19 days; calibration age 41 days; network status normal;
sensor variance 0.31.

DIAGNOSIS: sensor drift, confidence 0.68.

INVESTIGATION PATH: get_machine_metrics(M2) -> sensor variance 0.31, calibration 41 days;
get_recent_events(M2) -> no config change.
DECISIVE EVIDENCE: stale calibration with high sensor variance and no config change
(5 tool calls; 2 were decisive).

ACTIONS ATTEMPTED:
1. ROLLBACK_CONFIG -> no_effect (78%)
2. RECALIBRATE_SENSOR -> full_recovery (96%)

RESOLUTION: final action RECALIBRATE_SENSOR, MTTR 12.0 sim-minutes.
OUTCOME: successful.
LESSON: When calibration is stale and config is unchanged, rollback does nothing;
recalibrate the sensor.""",
    },
]

# Paraphrased on purpose: no log line or version string is copied from the episodes.
QUERIES = {
    "config_regression_paraphrased": (
        "machine showing slow steady output loss with axis drives not responding in time, "
        "configuration updated recently, calibration recent",
        {"INC-S01", "INC-S02"},
    ),
    "sensor_drift_paraphrased": (
        "machine losing output with false overheating alarms and noisy probe readings, "
        "no recent configuration change, calibration overdue",
        {"INC-S03"},
    ),
}


def fmt(v: float | None) -> str:
    return "  -  " if v is None else f"{v:.3f}"


def recall_and_report(client: Hindsight, bank_id: str, label: str, query: str, expected: set[str]):
    t0 = time.perf_counter()
    resp = client.recall(
        bank_id=bank_id,
        query=query,
        types=["world", "experience", "observation"],
        budget="mid",
        max_tokens=4096,
        include_source_facts=True,
    )
    elapsed = time.perf_counter() - t0
    print(f"\n=== {label}  ({len(resp.results)} results in {elapsed:.2f}s)")
    print(f"query: {query}")
    print(f"{'type':<12}{'incident':<10}{'semantic':>9}{'rerank':>8}{'final':>8}  text")

    per_incident: dict[str, list[float]] = defaultdict(list)
    for r in resp.results:
        s = r.scores
        inc = (r.metadata or {}).get("incident_id", "-")
        print(
            f"{r.type or '-':<12}{inc:<10}{fmt(s.semantic if s else None):>9}"
            f"{fmt(s.reranker if s else None):>8}{fmt(s.final if s else None):>8}  "
            f"{r.text[:90]}"
        )
        if inc != "-" and s is not None:
            best = s.semantic if s.semantic is not None else s.reranker
            if best is not None:
                per_incident[inc].append(best)

    print("\nper-incident match score (max semantic, reranker fallback):")
    for inc in sorted({e["incident_id"] for e in EPISODES}):
        scores = per_incident.get(inc)
        score = max(scores) if scores else None
        tag = "TRUE " if inc in expected else "false"
        print(f"  {tag} {inc}: {fmt(score)}  ({len(scores or [])} facts)")
    return {inc: max(v) for inc, v in per_incident.items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="don't delete the spike bank")
    parser.add_argument(
        "--observation-wait",
        type=int,
        default=90,
        help="seconds to wait for observations to appear",
    )
    args = parser.parse_args()

    s = get_settings()
    if not s.hindsight_api_key:
        raise SystemExit("HINDSIGHT_API_KEY is not set (see .env.example)")
    bank_id = f"memoryops-spike-{int(time.time())}"
    client = Hindsight(base_url=s.hindsight_base_url, api_key=s.hindsight_api_key, timeout=120)

    print(f"Creating bank {bank_id}")
    client.create_bank(
        bank_id=bank_id,
        name="MemoryOps spike",
        mission="I am a production incident responder for a 5-machine factory. I learn from "
        "every incident resolution to diagnose faster and more accurately.",
        retain_mission="Extract incident symptoms, the evidence that identified the root cause, "
        "which remediation actions worked or failed and why, and time to recovery.",
    )

    try:
        base_ts = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)
        for i, ep in enumerate(EPISODES):
            t0 = time.perf_counter()
            resp = client.retain(
                bank_id=bank_id,
                content=ep["text"],
                context="production incident resolution",
                timestamp=base_ts + timedelta(days=i),
                document_id=ep["incident_id"],
                metadata={
                    k: ep[k] for k in ("incident_id", "machine_id", "diagnosis", "final_action")
                }
                | {"record_kind": "episode"},
                tags=["kind:episode"],
                retain_async=False,
            )
            print(
                f"retain {ep['incident_id']}: success={resp.success} "
                f"in {time.perf_counter() - t0:.2f}s usage={resp.usage}"
            )

        # Immediately after retain: is it recallable, and how do scores separate?
        results = {}
        for label, (query, expected) in QUERIES.items():
            results[label] = (recall_and_report(client, bank_id, label, query, expected), expected)

        print("\n=== threshold guidance")
        true_scores = [sc for m, exp in results.values() for inc, sc in m.items() if inc in exp]
        false_scores = [
            sc for m, exp in results.values() for inc, sc in m.items() if inc not in exp
        ]
        print(f"true-match scores:  {sorted(round(x, 3) for x in true_scores)}")
        print(f"false-match scores: {sorted(round(x, 3) for x in false_scores)}")
        if true_scores and false_scores:
            lo, hi = min(true_scores), max(false_scores)
            verdict = "separable" if lo > hi else "OVERLAP - threshold alone is not enough"
            print(
                f"min true {lo:.3f} vs max false {hi:.3f} -> {verdict}; "
                f"suggested threshold ~{(lo + hi) / 2:.3f}"
            )

        print(f"\n=== waiting up to {args.observation_wait}s for observations")
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < args.observation_wait:
            obs = client.recall(
                bank_id=bank_id,
                query=QUERIES["config_regression_paraphrased"][0],
                types=["observation"],
                budget="low",
            )
            if obs.results:
                print(f"{len(obs.results)} observations after {time.perf_counter() - t0:.0f}s:")
                for r in obs.results[:5]:
                    print(f"  - {r.text[:140]}")
                break
            time.sleep(5)
        else:
            print("no observations yet (consolidation still running) - fine, not on critical path")
    finally:
        if args.keep:
            print(f"\nKept bank {bank_id}")
        else:
            client.delete_bank(bank_id)
            print(f"\nDeleted bank {bank_id}")
        client.close()


if __name__ == "__main__":
    main()
