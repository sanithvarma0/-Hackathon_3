"""Episode rendering, query paraphrasing and the reranker match rule (BUILD_PLAN 6.2, 6.3)."""

import re
from types import SimpleNamespace

from backend.agent.models import InvestigationSummary
from backend.memory.hindsight import apply_match_rule
from backend.memory.render import (
    AttemptRecord,
    EpisodeInput,
    evidence_query,
    generalize,
    hints_query,
    paraphrase,
    render_episode,
)
from backend.schemas import Alert

ALERT = Alert(
    incident_id="INC-004",
    machine_id="M4",
    machine_name="5-axis machining center",
    machine_profile="DMG Mori NVX 5080",
    detected_ts=1_790_000_000,
    throughput_pct=66.0,
    nominal_throughput_pct=97.0,
    alerting_machines=("M4",),
)


def test_paraphrase_rewrites_log_wording_and_drops_identifiers():
    out = paraphrase("Servo timeout on axis J4 after config v9.4.0 deployed 12 min before on M3")
    assert "axis drive response timeouts" in out
    assert not re.search(r"\d|M3|v9", out)


def test_evidence_query_contains_only_observed_signals_without_numbers():
    summary = InvestigationSummary(
        onset="gradual",
        key_signals=[
            "servo timeout errors on M3",
            "config v9.4.0 deployed 12 min before onset",
            "cycle time +31%",
        ],
    )
    query = evidence_query(summary)
    assert query.startswith("gradual output decline")
    assert "a new configuration was deployed" in query
    assert not re.search(r"\d", query)


def test_hints_query_distinguishes_local_and_shared_problems():
    assert "5-axis machining center" in hints_query(ALERT)
    shared = ALERT.model_copy(update={"alerting_machines": ("M1", "M2", "M5")})
    assert "several machines" in hints_query(shared)


def test_generalize_removes_ids_and_numbers():
    assert generalize("M3 lost 38% after v2.15.0") == "the machine lost after"


def _episode(attempts: list[AttemptRecord], outcome: str = "successful") -> EpisodeInput:
    return EpisodeInput(
        incident_id="INC-004",
        ts=1_790_000_000,
        machine_id="M4",
        machine_profile="5-axis machining center",
        signature="gradual decline on M4 after v3.3.1",
        alert=ALERT,
        summary=InvestigationSummary(onset="gradual", decisive_steps=[2]),
        diagnosis="config regression",
        confidence=0.8,
        tool_path=["get_recent_events(M4, 60)"],
        tool_calls=3,
        attempts=attempts,
        outcome=outcome,
        mttr_sim_s=540,
        cited_incidents=["INC-001"],
    )


def test_episode_has_the_generalized_signature_and_lesson():
    text = render_episode(
        _episode(
            [
                AttemptRecord("RESTART_MACHINE", "partial_recovery", 92.0, 67.0),
                AttemptRecord("ROLLBACK_CONFIG", "full_recovery", 97.0, 96.0),
            ]
        )
    )
    assert "SIGNATURE: gradual decline on the machine after" in text
    assert "recovered to 92%, then fell back to 67%" in text
    assert (
        "LESSON: For this signature RESTART_MACHINE only gave temporary relief; "
        "ROLLBACK_CONFIG resolved it. Go straight to ROLLBACK_CONFIG."
    ) in text
    assert "Memory cited: INC-001" in text and "MTTR 9.0 sim-minutes" in text


def test_escalated_episode_lesson():
    text = render_episode(_episode([AttemptRecord("ESCALATE_HUMAN", "escalated")], "escalated"))
    assert "escalate to a human engineer early" in text


def fact(incident_id: str, rerank: float, text: str = "fact", kind: str = "episode"):
    return SimpleNamespace(
        text=text,
        metadata={
            "incident_id": incident_id,
            "record_kind": kind,
            "final_action": "ROLLBACK_CONFIG",
            "diagnosis": "config regression",
            "outcome": "successful",
        },
        scores=SimpleNamespace(reranker=rerank, semantic=0.71),
    )


def test_match_rule_is_relative_to_the_top_result():
    """Measured in the spike: true >= 0.245 x top, false <= 0.056 x top (BUILD_PLAN 14.1)."""
    facts = [
        fact("INC-001", 0.26),
        fact("INC-002", 0.064),
        fact("INC-003", 0.009),
        fact("INC-001", 0.10, "second fact"),
    ]
    matches = apply_match_rule(facts, rel=0.15, floor=0.05)
    assert [m.incident_id for m in matches] == ["INC-001", "INC-002"]
    assert matches[0].rank == 1 and matches[0].strength == "strong"
    assert matches[1].strength == "weak"
    assert matches[0].facts == ("fact", "second fact")  # best fact first


def test_match_rule_abstains_when_nothing_is_relevant():
    assert apply_match_rule([fact("INC-001", 0.02)], rel=0.15, floor=0.05) == []


def test_match_rule_excludes_the_current_incident():
    facts = [fact("INC-005", 0.9, kind="lesson"), fact("INC-001", 0.5)]
    matches = apply_match_rule(facts, rel=0.15, floor=0.05, exclude_incident="INC-005")
    assert [m.incident_id for m in matches] == ["INC-001"]


def test_paraphrase_matches_whole_words_only():
    """Regression (live dry run): "mes" inside "times" became "tilost connections"."""
    out = paraphrase("longer cycle times, MES heartbeat missed and control loop jitter")
    assert "tilost" not in out
    assert out.count("lost connections to plant systems") == 1
    assert "longer cycle times" in out and "motion control errors" in out


def test_paraphrase_is_idempotent_and_leaves_no_orphans():
    """Regression (live dry run): "a a new configuration was deployed was deployed"."""
    once = paraphrase("config v3.2.1 -> v3.3.0 deployed at 16:57 (7 min before alert)")
    assert once == "a new configuration was deployed before alert"
    assert paraphrase(once) == once
    assert paraphrase("calibration overdue (41 days)") == "sensor calibration overdue"


def test_throughput_only_signals_are_dropped_from_the_query():
    summary = InvestigationSummary(
        onset="sudden",
        key_signals=[
            "throughput dropped from ~99% to ~89% within minutes",
            "config v3.2.1 -> v3.3.0 deployed 7 min before alert",
        ],
    )
    assert evidence_query(summary) == (
        "abrupt output decline; a new configuration was deployed before alert"
    )


def test_statements_of_absence_are_not_query_signals():
    """Regression (live dry run, OpenAI): "no recent events", "other machines healthy"."""
    summary = InvestigationSummary(
        onset="gradual",
        key_signals=[
            "temperature alarm while the IR probe still reads normal",
            "no recent events were found for M2 in the last 180 min",
            "M1 on the same gateway and M3/M4 on the other gateway were healthy",
            "calibration age 41 days, overdue",
        ],
    )
    assert evidence_query(summary) == (
        "gradual output decline; false overheating alarms while the ir probe still reads normal; "
        "sensor calibration overdue"
    )
