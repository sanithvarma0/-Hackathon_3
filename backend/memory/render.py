"""Episode/lesson prose and recall queries (BUILD_PLAN.md 6.2, 6.3).

Hindsight extracts facts from prose, so records are rendered as readable text in a fixed
template. Queries are built from *observed* signals only and paraphrased deterministically with
all IDs, versions and numbers removed — measured in the Phase 0.5 spike to be what separates
true matches from false ones (BUILD_PLAN.md 14.1).
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from backend.agent.models import InvestigationSummary
from backend.schemas import Alert

# Deterministic paraphrases: the query never repeats log wording verbatim.
PARAPHRASES: tuple[tuple[str, str], ...] = (
    (r"servo (timeout|time-?out)s?", "axis drive response timeouts"),
    (r"cycle[- ]time (increase|up|\+)\w*|longer cycle times?|cycle time", "longer cycle times"),
    (r"(control loop )?jitter|position error", "motion control errors"),
    (r"feed override clamp\w*", "feed rate limited by the controller"),
    (
        # idempotent: also matches its own output, so re-paraphrasing changes nothing
        r"(?:a\s+)?(?:new|recent)\s+config(?:uration)?(?:\s+(?:was|were))?"
        r"(?:\s+(?:deployed|pushed|released|applied|changed|updated))?"
        r"|config(?:uration)?\b[^;,]*?\b(?:deploy|chang|updat|push|releas)\w*",
        "a new configuration was deployed",
    ),
    (r"(packet|frame) loss", "dropped network packets"),
    (
        r"(high|elevated) latency|latency spikes?|slow (round[- ]trip|responses?)",
        "slow network responses",
    ),
    (
        r"(?:opc ?ua|mes|plc)(?:\s*(?:<->|-|to)?\s*scada)?(?:\s+(?:session|heartbeat))?"
        r"(?:\s+(?:timeouts?|timed out|missed|failures?|lost))?"
        r"|heartbeats?\s+missed|sessions?\s+timed\s+out",
        "lost connections to plant systems",
    ),
    (
        r"calibration\s+(is\s+)?(overdue|old|stale|expired|aged?)\w*|(overdue|old|stale|expired)"
        r"\s+calibration",
        "sensor calibration overdue",
    ),
    (
        r"phantom\s+\w+( alarms?)?|false (temperature|overheating|thermal)\w*( alarms?)?"
        r"|temperature alarms?",
        "false overheating alarms",
    ),
    (
        r"(sensor|probe)\s+(variance|noise|repeatability)\w*|noisy (sensor|probe)\w*",
        "noisy probe readings",
    ),
    (
        r"oom( killer| kills?)?|out[- ]of[- ]memory( kills?)?",
        "processes killed for running out of memory",
    ),
    (
        r"memory\s+(steadily\s+)?(climb|ris|grow|leak|increas)\w*",
        "controller memory steadily rising",
    ),
)
_TOKEN_WITH_DIGIT = re.compile(r"\S*\d\S*")
_MACHINE_ID = re.compile(r"\b(M\d|GW-[AB])\b", re.IGNORECASE)
_SPACES = re.compile(r"\s+")
# Words that only made sense next to a number that has now been removed.
_UNITS = {
    "min",
    "mins",
    "minute",
    "minutes",
    "h",
    "hr",
    "hrs",
    "hour",
    "hours",
    "s",
    "sec",
    "secs",
    "second",
    "seconds",
    "ago",
    "ms",
    "points",
    "percent",
    "approximately",
    "about",
    "roughly",
    "~",
    "day",
    "days",
    "week",
    "weeks",
    "mb",
    "gb",
}
_LINKERS = {
    "from",
    "to",
    "by",
    "at",
    "over",
    "within",
    "of",
    "in",
    "around",
    "near",
    "and",
    "after",
    "before",
    "on",
    "since",
    "for",
}
# Signals about throughput itself add nothing: the query already starts "output decline".
_REDUNDANT = re.compile(r"\b(throughput|oee|output)\b", re.IGNORECASE)


def _compile(pattern: str) -> re.Pattern[str]:
    # Whole-word matching: measured bug without it — "mes" inside "times" became a phrase.
    return re.compile(rf"\b(?:{pattern})\b")


_PARAPHRASE_RES = tuple((_compile(p), r) for p, r in PARAPHRASES)


def _drop_orphans(words: list[str]) -> list[str]:
    words = [w for w in words if w not in _UNITS]
    out: list[str] = []
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        if w in _LINKERS and (nxt is None or nxt in _LINKERS or nxt in (",", ";")):
            continue
        out.append(w)
    while out and out[-1] in _LINKERS:
        out.pop()
    return out


def paraphrase(signal: str) -> str:
    text = signal.strip().lower()
    for pattern, replacement in _PARAPHRASE_RES:
        text = pattern.sub(replacement, text)
    text = _MACHINE_ID.sub("", text)
    text = _TOKEN_WITH_DIGIT.sub("", text)
    text = re.sub(r"[()\[\]{}:=%\u2192>]", " ", text)
    words = _drop_orphans(text.replace(",", " , ").replace(";", " ; ").split())
    return _SPACES.sub(" ", " ".join(words)).replace(" ,", ",").replace(" ;", ";").strip(" ,;.-")


def generalize(text: str) -> str:
    """Strip machine IDs, versions and numbers (used for the SIGNATURE line)."""
    text = _MACHINE_ID.sub("the machine", text)
    text = _TOKEN_WITH_DIGIT.sub("", text)
    return _SPACES.sub(" ", text).strip()


def hints_query(alert: Alert) -> str:
    """Before investigating: what the alert alone says (memory touchpoint #1)."""
    scope = (
        "several machines losing output at the same time"
        if len(alert.alerting_machines) > 1
        else f"a {alert.machine_name.lower()} losing output"
    )
    return f"{scope}; what evidence identified the root cause and which fix worked"


def evidence_query(summary: InvestigationSummary) -> str:
    """After investigating: observed signals only, paraphrased (memory touchpoint #2)."""
    onset = {"gradual": "gradual", "sudden": "abrupt"}.get(summary.onset, "")
    signals: list[str] = []
    for s in summary.key_signals:
        if _REDUNDANT.search(s) and not re.search(
            r"config|calibrat|memory|packet|latency", s, re.I
        ):
            continue
        p = paraphrase(s)
        if p and p not in signals:
            signals.append(p)
    head = f"{onset} output decline".strip()
    return "; ".join([head, *signals[:6]])[:600]


@dataclass(frozen=True)
class AttemptRecord:
    action: str
    effect: str  # full_recovery | partial_recovery | no_effect | escalated (observed, not told)
    peak_throughput_pct: float | None = None
    min_throughput_pct: float | None = None


@dataclass(frozen=True)
class EpisodeInput:
    incident_id: str
    ts: int
    machine_id: str
    machine_profile: str
    signature: str
    alert: Alert
    summary: InvestigationSummary
    diagnosis: str
    confidence: float
    tool_path: list[str]
    tool_calls: int
    attempts: list[AttemptRecord]
    outcome: str  # successful | escalated
    mttr_sim_s: int
    cited_incidents: list[str] = field(default_factory=list)


def _fmt_attempt(i: int, a: AttemptRecord) -> str:
    if a.effect == "partial_recovery":
        detail = (
            f" (recovered to {a.peak_throughput_pct:.0f}%, then fell back to "
            f"{a.min_throughput_pct:.0f}% within the verify window)"
        )
    elif a.effect == "no_effect" and a.min_throughput_pct is not None:
        detail = f" (throughput stayed near {a.min_throughput_pct:.0f}%)"
    else:
        detail = ""
    return f"{i}. {a.action} -> {a.effect}{detail}"


def lesson_line(ep: EpisodeInput) -> str:
    failed = [a for a in ep.attempts if a.effect in ("partial_recovery", "no_effect")]
    final = ep.attempts[-1].action if ep.attempts else "ESCALATE_HUMAN"
    if ep.outcome == "escalated":
        tried = ", ".join(a.action for a in failed) or "no whitelisted fix"
        return (
            f"For this signature {tried} did not resolve the problem; escalate to a human "
            f"engineer early."
        )
    if not failed:
        return f"This signature is resolved by {final}; apply it first."
    temporary = [a.action for a in failed if a.effect == "partial_recovery"]
    useless = [a.action for a in failed if a.effect == "no_effect"]
    parts = []
    if temporary:
        parts.append(f"{', '.join(temporary)} only gave temporary relief")
    if useless:
        parts.append(f"{', '.join(useless)} had no effect")
    return f"For this signature {' and '.join(parts)}; {final} resolved it. Go straight to {final}."


def render_episode(ep: EpisodeInput) -> str:
    when = datetime.fromtimestamp(ep.ts, UTC).strftime("%Y-%m-%d %H:%M UTC")
    s = ep.summary
    drop = ep.alert.nominal_throughput_pct - ep.alert.throughput_pct
    attempts = "\n".join(_fmt_attempt(i + 1, a) for i, a in enumerate(ep.attempts)) or "none"
    cited = f" Memory cited: {', '.join(ep.cited_incidents)}." if ep.cited_incidents else ""
    return "\n".join(
        [
            f"INCIDENT {ep.incident_id} - {when}",
            f"MACHINE: {ep.machine_id} ({ep.machine_profile})",
            f"SIGNATURE: {generalize(ep.signature)}",
            "",
            f"SYMPTOMS: throughput down {drop:.0f} points from nominal at detection, "
            f"onset {s.onset}; observed signals: {'; '.join(s.key_signals) or 'none recorded'}.",
            f"CONTEXT: what changed: {s.what_changed or 'nothing identified'}; ruled out: "
            f"{'; '.join(s.ruled_out) or 'nothing'}.",
            "",
            f"DIAGNOSIS: {ep.diagnosis}, confidence {ep.confidence:.2f}.{cited}",
            "",
            f"INVESTIGATION PATH: {'; '.join(ep.tool_path) or 'none'}",
            f"DECISIVE EVIDENCE: {s.decisive_evidence or 'not identified'} "
            f"({ep.tool_calls} tool calls; {len(s.decisive_steps)} decisive).",
            "",
            "ACTIONS ATTEMPTED:",
            attempts,
            "",
            f"RESOLUTION: final action {ep.attempts[-1].action if ep.attempts else 'none'}, "
            f"MTTR {ep.mttr_sim_s / 60:.1f} sim-minutes.",
            f"OUTCOME: {ep.outcome}.",
            f"LESSON: {lesson_line(ep)}",
        ]
    )


def render_lesson(
    incident_id: str,
    attempt_no: int,
    machine_id: str,
    signature: str,
    attempt: AttemptRecord,
    decisive_evidence: str,
) -> str:
    what = (
        f"gave only temporary relief: throughput recovered to {attempt.peak_throughput_pct:.0f}% "
        f"then fell back to {attempt.min_throughput_pct:.0f}% within the verify window"
        if attempt.effect == "partial_recovery"
        else "had no effect on throughput"
    )
    return "\n".join(
        [
            f"LESSON FROM INCIDENT {incident_id} (attempt {attempt_no}, machine {machine_id})",
            f"SIGNATURE: {generalize(signature)}",
            f"EVIDENCE: {decisive_evidence or 'see incident record'}",
            f"FINDING: {attempt.action} {what}.",
            f"LESSON: Do not rely on {attempt.action} for this signature; it does not fix the "
            f"root cause.",
        ]
    )
