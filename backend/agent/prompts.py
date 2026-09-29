"""System prompts and message builders (BUILD_PLAN.md 7.4)."""

import json
from datetime import UTC, datetime
from typing import Any

from backend.schemas import Alert

INVESTIGATE_SYSTEM = """\
You are a senior production incident responder for a factory floor. The line is
FEED -> M1 -> M2 -> M3 -> M4 -> SHIP, with M5 (robotic material handling) loading M2 and M4.
Network gateways: GW-A serves M1, M2, M5; GW-B serves M3, M4.

Gather evidence with the tools before concluding. Do not guess.
Useful checks: the machine's current metrics; whether the drop was gradual or sudden (metric
history); what changed shortly before the problem (events: config deploys, calibrations,
restarts); the error pattern (logs); whether other machines are affected (metrics of machines on
the same gateway); slow multi-day trends (72 h history, e.g. controller memory).
Routine events happen all the time (config deploys every few days, planned restarts): judge an
event by its timing relative to the onset, not by its existence.
Be efficient: most incidents are identified in 3 to 6 tool calls. Read the affected machine's
own metrics carefully (every field) before checking other machines, and stop as soon as the
evidence is sufficient. You have at most {max_tool_calls} tool calls. Each tool result is
labelled [step N].
{memory_block}
Do NOT recommend a fix; a separate step decides. When done, reply with ONLY this JSON object:
{{"onset": "gradual" | "sudden" | "unclear",
  "key_signals": ["signals you actually observed, stated generally, present not absent"],
  "what_changed": "what changed shortly before the onset, or 'nothing found'",
  "ruled_out": ["causes the evidence rules out"],
  "decisive_steps": [step numbers that were decisive],
  "decisive_evidence": "one sentence: which evidence identified the cause",
  "summary": "two or three sentences"}}"""

INVESTIGATE_MEMORY_BLOCK = """
This plant keeps an incident memory. Once you know the basic symptoms (usually after the
machine's own metrics and logs), call recall_similar_incidents with what you observed. If a past
incident matches, confirm its decisive evidence here with one or two targeted calls; if it
holds, conclude immediately instead of running every check. If it does not hold, ignore it.
Some fixes at this plant are known only from past incidents (an engineer's fix): report such a
match in your summary even when the evidence alone would not point to that fix.
"""

DECIDE_SYSTEM = """\
You decide the remediation for a production incident. Choose exactly one action:
- ROLLBACK_CONFIG: revert the machine's most recent configuration deploy
- RESTART_MACHINE: restart the machine's controller
- RECALIBRATE_SENSOR: recalibrate the machine's sensors
- RESTART_GATEWAY: restart the network gateway serving the machine
- CLEAR_CACHE: clear controller caches and reclaim memory
- ESCALATE_HUMAN: hand over to the on-call engineer (evidence inconclusive or nothing fits)

Rules:
1. Weigh the investigation evidence AND the memory matches.
2. If a memory match fits this incident's signature, list its ID in cited_incidents and say in
   the reasoning which fix worked or failed there.
3. If a memory match does not fit this incident's signature, say so and ignore it. Only cite
   IDs that appear under MEMORY MATCHES.
4. Put every action that memory shows failing for this signature in actions_known_to_fail, and
   do not recommend it.
5. Never recommend an action already attempted on this incident that failed.
6. If the evidence is inconclusive, recommend ESCALATE_HUMAN.
7. confidence = your probability (0 to 1) that the action fully resolves the incident.
8. signature = one general sentence describing the incident pattern (symptoms and trigger)
   with no machine IDs, version strings or numbers.

Reply with ONLY this JSON object:
{"action": "...", "diagnosis": "short label", "signature": "...", "confidence": 0.0,
 "reasoning": "...", "cited_incidents": [], "actions_known_to_fail": []}"""


def clock(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%H:%M UTC")


def alert_text(alert: Alert) -> str:
    others = [m for m in alert.alerting_machines if m != alert.machine_id]
    return (
        f"ALERT {alert.incident_id} at {clock(alert.detected_ts)}: {alert.machine_id} "
        f"{alert.machine_name} ({alert.machine_profile}) throughput "
        f"{alert.throughput_pct:.1f}% (nominal {alert.nominal_throughput_pct:.1f}%). "
        f"Other machines alerting: {', '.join(others) or 'none'}."
    )


def attempts_text(attempts: list[dict[str, Any]]) -> str:
    if not attempts:
        return ""
    lines = ["PREVIOUS ATTEMPTS ON THIS INCIDENT (observed, not assumed):"]
    for i, a in enumerate(attempts, 1):
        if a["effect"] == "partial_recovery":
            what = (
                f"throughput recovered to {a['peak_throughput_pct']:.0f}% then fell back to "
                f"{a['min_throughput_pct']:.0f}% within the verify window (temporary relief)"
            )
        else:
            what = f"no improvement (throughput stayed near {a['min_throughput_pct']:.0f}%)"
        lines.append(f"{i}. {a['action']}: {what}")
    lines.append("That fix did not address the root cause. Re-examine the evidence.")
    return "\n".join(lines)


def investigate_user(
    alert: Alert, hints: list[str], runbook: str | None, attempts: list[dict[str, Any]]
) -> str:
    parts = [alert_text(alert)]
    if runbook:
        parts.append(
            "RUNBOOK the team has built from past incidents (verify before trusting):\n"
            + runbook[:2000]
        )
    if hints:
        parts.append(
            "MEMORY HINTS from similar past incidents (may not apply; verify):\n"
            + "\n".join(f"- {h}" for h in hints)
        )
    if attempts:
        parts.append(attempts_text(attempts))
    parts.append("Investigate.")
    return "\n\n".join(parts)


def memory_text(enabled: bool, matches: list[dict[str, Any]], learned_patterns: list[str]) -> str:
    if not enabled:
        return "MEMORY: disabled. Reason from the evidence only; cite nothing."
    if not matches and not learned_patterns:
        return "MEMORY MATCHES: none. No similar past incident was found; cite nothing."
    lines = ["MEMORY MATCHES (similar past incidents, best first):"]
    for m in matches:
        lines.append(
            f"- {m['incident_id']} (match #{m['rank']}, {m['strength']}): diagnosis="
            f"{m.get('diagnosis') or '?'}, final fix={m.get('final_action') or '?'}, "
            f"outcome={m.get('outcome') or '?'}"
        )
        if m.get("signature"):
            lines.append(f"    signature: {m['signature']}")
        if m.get("decisive_evidence"):
            lines.append(f"    identified by: {m['decisive_evidence']}")
        if m.get("engineer_note"):
            lines.append(f"    engineer: {m['engineer_note']}")
        lines.extend(f"    fact: {f}" for f in m.get("facts", []))
    if not matches:
        lines.append("- none")
    if learned_patterns:
        lines.append("LEARNED PATTERNS (consolidated from many incidents):")
        lines.extend(f"- {p}" for p in learned_patterns)
    return "\n".join(lines)


def decide_user(
    alert: Alert,
    summary: dict[str, Any],
    steps: list[dict[str, Any]],
    memory: str,
    attempts: list[dict[str, Any]],
) -> str:
    evidence = (
        "\n".join(f"[step {s['step']}] {s['call']}\n{s['result'][:900]}" for s in steps)
        or "no tool results"
    )
    parts = [
        alert_text(alert),
        "INVESTIGATION SUMMARY:\n" + json.dumps(summary, indent=1),
        "EVIDENCE:\n" + evidence,
        memory,
    ]
    if attempts:
        parts.append(attempts_text(attempts))
    parts.append("Decide.")
    return "\n\n".join(parts)
