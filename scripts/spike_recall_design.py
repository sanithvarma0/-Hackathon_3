"""Phase 0.5 follow-up: which episode/query design lets recall separate true from false matches?

spike_hindsight.py showed semantic cosine scores for true and false matches overlap
(~0.69-0.76 for every factory incident). This compares two record designs over five incidents
covering all four incident classes:

  nosig  episode prose only
  sig    episode prose + a one-line generalized `SIGNATURE:` (what the agent writes in `learn`)

and queries phrased from *observed* signals only (no "X is absent" clauses, which pull in
the incidents where X mattered).

Measured result (2026-09-28, Hindsight Cloud): with `sig`, the true incident ranks first for
all 5 queries and the best false match's reranker score is <= 0.056 x the top score, while the
weakest true match is >= 0.245 x the top. Semantic margins stay within +/-0.08 either way.
-> gate matches on reranker score relative to the top result (BUILD_PLAN.md 6.3).

Run: uv run python scripts/spike_recall_design.py sig|nosig
"""

import sys
import time
from collections import defaultdict

from hindsight_client import Hindsight

from backend.config import get_settings

SIGNATURES = {
    "config_regression": "gradual throughput decline that began shortly after a configuration "
    "deployment on the machine; controller timing and motion errors",
    "sensor_drift": "gradual throughput decline with implausible sensor readings and phantom "
    "temperature alarms on a machine whose sensor calibration is overdue; configuration unchanged",
    "network_failure": "sudden throughput collapse with packet loss and latency spikes affecting "
    "several machines behind the same network gateway",
    "resource_exhaustion": "controller memory usage climbing steadily over several days followed "
    "by out-of-memory process kills and throughput loss",
}

# (incident_id, machine, diagnosis, final_action, symptoms/context)
EPISODES = [
    (
        "INC-E01",
        "M3",
        "config_regression",
        "ROLLBACK_CONFIG",
        "throughput -38% over 6 min, gradual; logs: servo timeout on axis J4; cycle time +31%. "
        "Config v2.14.3->v2.15.0 deployed 11 min before onset. Calibration 9 days old. "
        "Network normal.",
    ),
    (
        "INC-E02",
        "M4",
        "config_regression",
        "ROLLBACK_CONFIG",
        "throughput -29% over 5 min, gradual; logs: control loop jitter, position error 0.12mm; "
        "feed override clamped at 70%. Config v3.2.0->v3.3.1 deployed 16 min before onset. "
        "RESTART_MACHINE gave only temporary relief (71%, re-degraded after 95s).",
    ),
    (
        "INC-E03",
        "M2",
        "sensor_drift",
        "RECALIBRATE_SENSOR",
        "throughput -22% over 8 min, gradual; logs: spindle temperature alarm 81C (IR reads 44C); "
        "probe variance high. Calibration 41 days old, sensor variance 0.31. Config unchanged for "
        "19 days. ROLLBACK_CONFIG had no effect.",
    ),
    (
        "INC-E04",
        "M1",
        "network_failure",
        "RESTART_GATEWAY",
        "throughput -61% instantly on M1 and M2; packet loss 12% on GW-A, latency 140ms. "
        "RESTART_MACHINE had no effect.",
    ),
    (
        "INC-E05",
        "M5",
        "resource_exhaustion",
        "CLEAR_CACHE",
        "memory 58%->93% over 3 days, OOM kill of hmi-cache process, throughput -24%. "
        "RESTART_MACHINE recovered to 90% then re-degraded after 140s.",
    ),
]

# (query built from observed signals only, expected incident IDs)
QUERIES = {
    "config_regression": (
        "gradual output decline beginning shortly after a new configuration was deployed; "
        "axis drive response timeouts",
        {"INC-E01", "INC-E02"},
    ),
    "sensor_drift": (
        "gradual output decline with false overheating alarms and noisy probe readings; "
        "sensor calibration overdue",
        {"INC-E03"},
    ),
    "network_failure": (
        "abrupt output collapse on two machines at once with dropped packets and high latency",
        {"INC-E04"},
    ),
    "resource_exhaustion": (
        "memory steadily rising for days, processes killed for running out of memory, "
        "output falling",
        {"INC-E05"},
    ),
}


def render(incident_id: str, machine: str, diagnosis: str, fix: str, body: str, sig: bool) -> str:
    text = f"INCIDENT {incident_id}\nMACHINE: {machine}\n"
    if sig:
        text += f"SIGNATURE: {SIGNATURES[diagnosis]}.\n"
    return (
        text + f"SYMPTOMS AND CONTEXT: {body}\nDIAGNOSIS: {diagnosis.replace('_', ' ')}.\n"
        f"RESOLUTION: {fix} -> full recovery."
    )


def main(sig: bool) -> None:
    s = get_settings()
    client = Hindsight(base_url=s.hindsight_base_url, api_key=s.hindsight_api_key, timeout=120)
    bank_id = f"memoryops-exp-{'sig' if sig else 'nosig'}-{int(time.time())}"
    client.create_bank(
        bank_id=bank_id,
        name="MemoryOps recall design spike",
        mission="Production incident responder for a 5-machine factory.",
        retain_mission="Extract incident symptoms, the evidence that identified the root cause, "
        "which remediation actions worked or failed and why, and time to recovery.",
    )
    try:
        for incident_id, machine, diagnosis, fix, body in EPISODES:
            client.retain(
                bank_id=bank_id,
                content=render(incident_id, machine, diagnosis, fix, body, sig),
                context="production incident resolution",
                document_id=incident_id,
                metadata={"incident_id": incident_id, "diagnosis": diagnosis, "final_action": fix},
                retain_async=False,
            )

        summary = []
        for label, (query, expected) in QUERIES.items():
            resp = client.recall(
                bank_id=bank_id, query=query, types=["world", "experience"], budget="mid"
            )
            semantic: dict[str, float] = defaultdict(float)
            rerank: dict[str, float] = defaultdict(float)
            first_rank: dict[str, int] = {}
            for i, r in enumerate(resp.results):
                inc = (r.metadata or {}).get("incident_id")
                if not inc or not r.scores:
                    continue
                semantic[inc] = max(semantic[inc], r.scores.semantic or 0)
                rerank[inc] = max(rerank[inc], r.scores.reranker or 0)
                first_rank.setdefault(inc, i)

            print(f"\n{label}: expected {sorted(expected)}")
            for inc in sorted(first_rank, key=first_rank.get):
                tag = "TRUE " if inc in expected else "false"
                print(
                    f"  {tag} {inc} first_rank={first_rank[inc]:2d} "
                    f"semantic={semantic[inc]:.3f} reranker={rerank[inc]:.3f}"
                )
            top = max(rerank.values(), default=0) or 1
            true_rr = [rerank[i] for i in expected]
            false_rr = [v for i, v in rerank.items() if i not in expected] or [0]
            true_sem = [semantic[i] for i in expected]
            false_sem = [v for i, v in semantic.items() if i not in expected] or [0]
            summary.append(
                (label, min(true_sem) - max(false_sem), min(true_rr) / top, max(false_rr) / top)
            )

        print(f"\n{'query':<22}{'semantic margin':>16}{'min true/top':>14}{'max false/top':>15}")
        for label, sem_margin, true_ratio, false_ratio in summary:
            print(f"{label:<22}{sem_margin:>16.3f}{true_ratio:>14.3f}{false_ratio:>15.3f}")
    finally:
        client.delete_bank(bank_id)
        client.close()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("sig", "nosig"):
        raise SystemExit("usage: spike_recall_design.py sig|nosig")
    main(sig=sys.argv[1] == "sig")
