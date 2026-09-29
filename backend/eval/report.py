"""Turn eval rows into the committed report (BUILD_PLAN.md 11.3-11.5).

`summarize(rows)` computes every number (the Learning tab reads the same summary through
`GET /api/eval/latest`); `write_report(...)` writes REPORT.md, results.json and charts/*.png.
Targets are judged here and printed PASS/FAIL — a miss is reported, never hidden.
"""

import json
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from backend.eval.sequence import family
from backend.eval.stats import (
    Estimate,
    bootstrap,
    brier,
    difference,
    mean,
    paired,
    reliability,
    wilson,
)

Row = dict[str, Any]
BUCKETS = ("1st", "2nd", "3rd+")
CONDS = ("memory_on", "memory_off")
CLASSES = (
    "config_regression",
    "sensor_drift",
    "network_failure",
    "resource_exhaustion",
    "vision_link_dropout",
    "servo_tuning_drift",
)
FAMILIES = ("textbook", "site_knowledge")
FAMILY_LABEL = {
    "textbook": "Textbook incidents (the fix follows from the evidence) — memory must not hurt",
    "site_knowledge": "Site-knowledge incidents (the fix is known only from a past resolution)",
}
FALSE_REPLAY_TARGET = 1 / 15
RECALL_AT_1_TARGET = 0.8

# name -> (row value, higher is better)
METRICS: dict[str, tuple[Callable[[Row], float | None], bool]] = {
    "accuracy": (lambda r: r["recommendation_correct"], True),
    # safe: the right fix, or a hand-over to a human — never a wrong fix applied to the plant
    "safe": (
        lambda r: int(
            bool(r["recommendation_correct"]) or r.get("first_recommendation") == "ESCALATE_HUMAN"
        ),
        True,
    ),
    "mttr_min": (lambda r: None if r["mttr_sim_s"] is None else r["mttr_sim_s"] / 60, False),
    "tool_calls": (lambda r: r["tool_calls"], False),
    "first_attempt_tool_calls": (lambda r: r["first_attempt_tool_calls"], False),
    "confidence": (lambda r: r["calibrated_confidence"], True),
}


def bucket(exposure: int) -> str:
    return BUCKETS[min(exposure, 3) - 1]


def _values(rows: Iterable[Row], metric: str) -> list[float]:
    get = METRICS[metric][0]
    return [float(v) for r in rows if (v := get(r)) is not None]


def _by_pair(rows: Iterable[Row], metric: str) -> dict[tuple[int, int], float]:
    get = METRICS[metric][0]
    return {(r["seed"], r["position"]): float(v) for r in rows if (v := get(r)) is not None}


def _est(e: Estimate | None) -> dict[str, float | int] | None:
    return None if e is None else e.as_dict()


def family_of(r: Row) -> str:
    return family(r["true_type"])


def exposure_table(cond: dict[str, list[Row]]) -> dict[str, dict[str, dict[str, Any]]]:
    table: dict[str, dict[str, dict[str, Any]]] = {}
    for metric in METRICS:
        table[metric] = {}
        for b in BUCKETS:
            on = [r for r in cond["memory_on"] if bucket(r["exposure"]) == b]
            off = [r for r in cond["memory_off"] if bucket(r["exposure"]) == b]
            table[metric][b] = {
                "memory_on": _est(bootstrap(_values(on, metric))),
                "memory_off": _est(bootstrap(_values(off, metric))),
                "diff": _est(paired(_by_pair(on, metric), _by_pair(off, metric))),
            }
    return table


def summarize(rows: list[Row], *, expected_units: int | None = None) -> dict[str, Any]:
    ok = [r for r in rows if not r.get("error")]
    cond = {c: [r for r in ok if r["condition"] == c] for c in CONDS}
    by_exposure = exposure_table(cond)
    by_family: dict[str, Any] = {}
    for fam in FAMILIES:
        fcond = {c: [r for r in cond[c] if family_of(r) == fam] for c in CONDS}
        if not any(fcond.values()):
            continue
        by_family[fam] = {
            "n": {c: len(v) for c, v in fcond.items()},
            "by_exposure": exposure_table(fcond),
            "paired_overall": {
                m: _est(paired(_by_pair(fcond["memory_on"], m), _by_pair(fcond["memory_off"], m)))
                for m in METRICS
            },
        }
    memory_tool = {
        "incidents_using_it": sum(1 for r in cond["memory_on"] if r.get("memory_tool_calls")),
        "n": len(cond["memory_on"]),
        "mean_calls": mean([float(r.get("memory_tool_calls") or 0) for r in cond["memory_on"]])
        if cond["memory_on"]
        else None,
    }

    per_class: dict[str, dict[str, dict[str, Any]]] = {}
    for cls in CLASSES:
        per_class[cls] = {}
        for metric in ("accuracy", "mttr_min", "tool_calls"):
            per_class[cls][metric] = {
                b: {
                    c: _est(
                        bootstrap(
                            _values(
                                [
                                    r
                                    for r in cond[c]
                                    if r["true_type"] == cls and bucket(r["exposure"]) == b
                                ],
                                metric,
                            )
                        )
                    )
                    for c in CONDS
                }
                for b in BUCKETS
            }

    paired_overall = {
        m: _est(paired(_by_pair(cond["memory_on"], m), _by_pair(cond["memory_off"], m)))
        for m in METRICS
    }

    # Transfer: the first exposure on a held-out machine (M4-M5) after two elsewhere.
    transfer: dict[str, Any] = {}
    for c in CONDS:
        first_held = [r for r in cond[c] if r["held_out"] and r["exposure"] == 3]
        k = sum(r["recommendation_correct"] for r in first_held)
        transfer[c] = _est(wilson(k, len(first_held)))
    ranks = [
        next((m["rank"] for m in r["matches"] if m["type"] == r["true_type"]), None)
        for r in cond["memory_on"]
        if r["held_out"] and r["exposure"] == 3
    ]
    transfer["same_class_rank"] = {
        "found": sum(x is not None for x in ranks),
        "n": len(ranks),
        "mean_rank": mean([x for x in ranks if x is not None]) if any(ranks) else None,
    }

    # Discrimination: could a fix be replayed from another class? (memory has another class)
    discrimination: dict[str, Any] = {}
    for c in CONDS:
        probes = [r for r in cond[c] if r["prior_other_class"]]
        sd = [r for r in cond[c] if r["after_config_regression"]]
        discrimination[c] = {
            "probes": {"k": sum(r["false_replay"] for r in probes), "n": len(probes)},
            "rate": _est(wilson(sum(r["false_replay"] for r in probes), len(probes))),
            "sensor_after_config": {"k": sum(r["false_replay"] for r in sd), "n": len(sd)},
        }

    # Retrieval (memory ON only): is the top match the same class, when one exists?
    eligible = [r for r in cond["memory_on"] if r["prior_same_class"]]
    top_right = sum(
        1 for r in eligible if r["matches"] and r["matches"][0]["type"] == r["true_type"]
    )
    all_matches = [(m, r["true_type"]) for r in cond["memory_on"] for m in r["matches"]]
    strong = [(m, t) for m, t in all_matches if m["strength"] == "strong"]
    no_prior = [r for r in cond["memory_on"] if not r["prior_same_class"] and r["matches"]]
    retrieval = {
        "recall_at_1": _est(wilson(top_right, len(eligible))),
        "gate_precision": _est(
            wilson(sum(m["type"] == t for m, t in all_matches), len(all_matches))
        ),
        "strong_precision": _est(wilson(sum(m["type"] == t for m, t in strong), len(strong))),
        "mean_matches": mean([len(r["matches"]) for r in cond["memory_on"]])
        if cond["memory_on"]
        else None,
        "matched_without_same_class_prior": {
            "k": len(no_prior),
            "n": sum(1 for r in cond["memory_on"] if not r["prior_same_class"]),
        },
    }

    calibration = {}
    for c in CONDS:
        probs = [
            r["calibrated_confidence"] for r in cond[c] if r["calibrated_confidence"] is not None
        ]
        outs = [
            r["recommendation_correct"] for r in cond[c] if r["calibrated_confidence"] is not None
        ]
        calibration[c] = {
            "brier": brier(probs, outs),
            "bins": [b.__dict__ for b in reliability(probs, outs)],
        }

    cost: dict[str, Any] = {}
    for c in CONDS:
        rs = cond[c]
        cost[c] = {
            key: mean([float(r[key] or 0) for r in rs]) if rs else None
            for key in (
                "llm_tokens",
                "cost_usd",
                "hindsight_billed_tokens",
                "hindsight_cost_usd",
                "runbook_refreshes",
                "agent_time_real_s",
            )
        }
    totals = {
        "llm_usd": sum(float(r.get("cost_usd") or 0) for r in rows),
        "hindsight_usd": sum(float(r.get("hindsight_cost_usd") or 0) for r in rows),
        "llm_tokens": sum(int(r.get("llm_tokens") or 0) for r in rows),
        "incidents": len(rows),
    }

    drivers: dict[str, Any] = {}
    for c in CONDS:
        drivers[c] = {}
        for b in BUCKETS:
            rs = [r for r in cond[c] if bucket(r["exposure"]) == b]
            drivers[c][b] = {
                "attempts": mean([r["attempts"] or 1 for r in rs]) if rs else None,
                "wrong_first_fix": sum(1 for r in rs if not r["first_time_right"]),
                "escalated": sum(r["escalated"] for r in rs),
                "n": len(rs),
            }

    per_seed: dict[str, dict[int, float]] = {c: {} for c in CONDS}
    for c in CONDS:
        seeds = sorted({r["seed"] for r in cond[c]})
        for s in seeds:
            per_seed[c][s] = mean([r["recommendation_correct"] for r in cond[c] if r["seed"] == s])

    failures = sorted(
        (
            {
                "condition": r["condition"],
                "seed": r["seed"],
                "position": r["position"],
                "incident_id": r["incident_id"],
                "true_type": r["true_type"],
                "machine_id": r["machine_id"],
                "exposure": r["exposure"],
                "recommended": r["first_recommendation"],
                "false_replay": bool(r["false_replay"]),
                "top_match": (r["matches"][0] if r["matches"] else None),
                "trace_url": r.get("trace_url"),
                "error": r.get("error"),
            }
            for r in rows
            if r.get("error") or not r["recommendation_correct"]
        ),
        key=lambda f: (f["true_type"], f["condition"], f["seed"], f["position"]),
    )

    summary: dict[str, Any] = {
        "rows": len(rows),
        "errors": sum(1 for r in rows if r.get("error")),
        "seeds": sorted({r["seed"] for r in rows}),
        "expected_units": expected_units,
        "by_exposure": by_exposure,
        "by_family": by_family,
        "memory_tool": memory_tool,
        "per_class": per_class,
        "paired_overall": paired_overall,
        "transfer": transfer,
        "discrimination": discrimination,
        "retrieval": retrieval,
        "calibration": calibration,
        "cost": cost,
        "totals": totals,
        "mttr_drivers": drivers,
        "per_seed_accuracy": per_seed,
        "failures": failures,
    }
    summary["targets"] = judge(summary, cond["memory_off"])
    return summary


def judge(summary: dict[str, Any], off_rows: list[Row]) -> list[dict[str, str]]:
    """The acceptance targets (11.5). PASS needs the evidence; anything else is FAIL."""
    late = {m: summary["by_exposure"][m]["3rd+"]["diff"] for m in METRICS}

    def excl(e: dict[str, Any] | None, sign: int) -> bool:
        return e is not None and ((sign > 0 and e["lo"] > 0) or (sign < 0 and e["hi"] < 0))

    first = [r for r in off_rows if r["exposure"] == 1]
    later = [r for r in off_rows if r["exposure"] >= 3]
    trends = {
        m: difference(_values(later, m), _values(first, m)) for m in ("accuracy", "tool_calls")
    }
    disc = summary["discrimination"]["memory_on"]
    recall = summary["retrieval"]["recall_at_1"]
    rate = disc["probes"]["k"] / disc["probes"]["n"] if disc["probes"]["n"] else None
    textbook = summary["by_family"].get("textbook")
    harm = textbook["paired_overall"]["accuracy"] if textbook else None

    def verdict(passed: bool, *evidence: object) -> str:
        if any(e is None for e in evidence):
            return "NO DATA"  # never tested is not the same as failed
        return "PASS" if passed else "FAIL"

    return [
        {
            "id": "1",
            "target": "Memory ON, 3rd+ exposure: recommendation accuracy higher than OFF "
            "(paired 95% CI excludes 0)",
            "status": verdict(excl(late["accuracy"], +1), late["accuracy"]),
            "evidence": fmt_diff(late["accuracy"], "accuracy"),
        },
        {
            "id": "2",
            "target": "Memory ON, 3rd+ exposure: tool calls and MTTR lower than OFF "
            "(paired 95% CIs exclude 0)",
            "status": verdict(
                excl(late["tool_calls"], -1) and excl(late["mttr_min"], -1),
                late["tool_calls"],
                late["mttr_min"],
            ),
            "evidence": f"tool calls {fmt_diff(late['tool_calls'], 'tool_calls')}; "
            f"MTTR {fmt_diff(late['mttr_min'], 'mttr_min')}",
        },
        {
            "id": "3",
            "target": "Memory OFF: no significant trend from 1st to 3rd+ exposure "
            "(the gain is memory, not drift)",
            "status": verdict(
                all(e is not None and not e.excludes_zero() for e in trends.values()),
                *trends.values(),
            ),
            "evidence": "; ".join(
                f"{m} {fmt_diff(None if e is None else e.as_dict(), m)}" for m, e in trends.items()
            ),
        },
        {
            "id": "4",
            "target": "Discrimination: false replays ≤ 1 per 15 probes (memory ON)",
            "status": verdict(rate is not None and rate <= FALSE_REPLAY_TARGET, rate),
            "evidence": f"{disc['probes']['k']} / {disc['probes']['n']} probes"
            + (f" ({rate:.1%})" if rate is not None else ""),
        },
        {
            "id": "5",
            "target": "Retrieval: recall@1 ≥ 0.8 when a same-class prior exists",
            "status": verdict(recall is not None and recall["mean"] >= RECALL_AT_1_TARGET, recall),
            "evidence": fmt_rate(recall),
        },
        {
            "id": "6",
            "target": "No harm: on textbook incidents memory does not lower accuracy "
            "(paired ON − OFF not significantly below 0)",
            "status": verdict(harm is not None and harm["hi"] >= 0, harm),
            "evidence": fmt_diff(harm, "accuracy"),
        },
    ]


# ---- formatting ---------------------------------------------------------------------------


def fmt(e: dict[str, Any] | None, metric: str) -> str:
    if e is None:
        return "—"
    if metric in ("accuracy", "confidence", "safe"):
        return f"{e['mean']:.0%} [{e['lo']:.0%}–{e['hi']:.0%}] n={e['n']}"
    if metric == "mttr_min":
        return f"{e['mean']:.1f} [{e['lo']:.1f}–{e['hi']:.1f}] n={e['n']}"
    return f"{e['mean']:.1f} [{e['lo']:.1f}–{e['hi']:.1f}] n={e['n']}"


def fmt_diff(e: dict[str, Any] | None, metric: str) -> str:
    if e is None:
        return "—"
    if metric in ("accuracy", "confidence", "safe"):
        return f"{e['mean']:+.0%} [{e['lo']:+.0%}, {e['hi']:+.0%}] (n={e['n']})"
    unit = " min" if metric == "mttr_min" else ""
    return f"{e['mean']:+.1f}{unit} [{e['lo']:+.1f}, {e['hi']:+.1f}] (n={e['n']})"


def fmt_rate(e: dict[str, Any] | None) -> str:
    if e is None:
        return "—"
    return f"{e['mean']:.0%} [{e['lo']:.0%}–{e['hi']:.0%}] (n={e['n']})"


LABEL = {
    "accuracy": "First recommendation correct",
    "safe": "Safe (right fix, or handed to a human)",
    "mttr_min": "MTTR (sim minutes)",
    "tool_calls": "Tool calls (all attempts)",
    "first_attempt_tool_calls": "Tool calls, first attempt",
    "confidence": "Stated confidence",
}


def markdown(summary: dict[str, Any], meta: dict[str, Any]) -> str:
    s = summary
    out: list[str] = []
    w = out.append
    complete = s["expected_units"] is None or meta.get("units_completed") == s["expected_units"]
    w(f"# MemoryOps evaluation — {meta.get('run_id', '')}")
    w("")
    if not complete:
        w(
            f"> **INCOMPLETE RUN** — {meta.get('units_completed')} of {s['expected_units']} "
            "units finished; numbers below cover only the finished units."
        )
        w("")
    w(
        f"{s['rows']} incident runs · seeds {s['seeds']} · memory ON vs OFF, paired (same "
        f"incidents, same order) · auto-approval · fast-forward clock "
        f"({meta.get('tool_call_sim_s')} sim-s per tool call) · git `{meta.get('git_sha')}`"
        f"{' (dirty tree)' if meta.get('git_dirty') else ''} · model "
        f"`{meta.get('llm_primary')}` · {meta.get('started_at')}"
    )
    w("")
    w("## Acceptance targets (BUILD_PLAN 11.5)")
    w("")
    w("| # | Target | Result | Evidence |")
    w("|---|---|---|---|")
    for t in s["targets"]:
        mark = {"PASS": "✅ PASS", "FAIL": "❌ FAIL"}.get(t["status"], "➖ NO DATA")
        w(f"| {t['id']} | {t['target']} | {mark} | {t['evidence']} |")
    w("")
    w("If a target is missed, the fix belongs in the agent or memory design — never in the metric.")
    w("")
    if s.get("by_family"):
        w("## By family")
        w("")
        for fam, data in s["by_family"].items():
            w(f"### {FAMILY_LABEL[fam]}")
            w("")
            w(f"n = {data['n']['memory_on']} incidents per condition.")
            w("")
            w(
                "| Exposure | Accuracy ON | Accuracy OFF | ON − OFF | Safe ON | Safe OFF | Tool calls ON | Tool calls OFF |"
            )
            w("|---|---|---|---|---|---|---|---|")
            t = data["by_exposure"]
            for b in BUCKETS:
                w(
                    f"| {b} | {fmt(t['accuracy'][b]['memory_on'], 'accuracy')} | "
                    f"{fmt(t['accuracy'][b]['memory_off'], 'accuracy')} | "
                    f"{fmt_diff(t['accuracy'][b]['diff'], 'accuracy')} | "
                    f"{fmt(t['safe'][b]['memory_on'], 'safe')} | {fmt(t['safe'][b]['memory_off'], 'safe')} | "
                    f"{fmt(t['tool_calls'][b]['memory_on'], 'tool_calls')} | "
                    f"{fmt(t['tool_calls'][b]['memory_off'], 'tool_calls')} |"
                )
            po = data["paired_overall"]
            w("")
            w(
                f"All exposures, paired ON − OFF: accuracy {fmt_diff(po['accuracy'], 'accuracy')}; "
                f"tool calls {fmt_diff(po['tool_calls'], 'tool_calls')}; "
                f"MTTR {fmt_diff(po['mttr_min'], 'mttr_min')}."
            )
            w("")
        mt = s.get("memory_tool")
        if mt and mt["n"]:
            w(
                f"The agent asked memory mid-investigation in {mt['incidents_using_it']} of "
                f"{mt['n']} memory-ON incidents ({mt['mean_calls']:.2f} calls per incident)."
            )
            w("")
    w("## Learning by exposure (all incidents)")
    w("")
    w("Mean [95% bootstrap CI]; ON − OFF is paired by (seed, position).")
    w("")
    for metric in ("accuracy", "safe", "mttr_min", "tool_calls", "confidence"):
        w(f"**{LABEL[metric]}**")
        w("")
        w("| Exposure | Memory ON | Memory OFF | ON − OFF (paired) |")
        w("|---|---|---|---|")
        for b in BUCKETS:
            e = s["by_exposure"][metric][b]
            w(
                f"| {b} | {fmt(e['memory_on'], metric)} | {fmt(e['memory_off'], metric)} | "
                f"{fmt_diff(e['diff'], metric)} |"
            )
        w("")
    w("![Learning curves](charts/learning_curves.png)")
    w("")
    w("![Paired differences](charts/paired_differences.png)")
    w("")
    w("### What drives MTTR")
    w("")
    w("| Exposure | Condition | Attempts (mean) | Wrong first fix | Escalated | n |")
    w("|---|---|---|---|---|---|")
    for b in BUCKETS:
        for c in CONDS:
            d = s["mttr_drivers"][c][b]
            att = "—" if d["attempts"] is None else f"{d['attempts']:.2f}"
            w(f"| {b} | {c} | {att} | {d['wrong_first_fix']} | {d['escalated']} | {d['n']} |")
    w("")
    w("## Per class")
    w("")
    w(
        "| Class | Exposure | Accuracy ON | Accuracy OFF | MTTR ON | MTTR OFF | Tools ON | Tools OFF |"
    )
    w("|---|---|---|---|---|---|---|---|")
    for cls in CLASSES:
        pc = s["per_class"][cls]
        if all(pc["accuracy"][b][c] is None for b in BUCKETS for c in CONDS):
            continue
        for b in BUCKETS:
            w(
                f"| {cls.replace('_', ' ')} | {b} | {fmt(pc['accuracy'][b]['memory_on'], 'accuracy')} | "
                f"{fmt(pc['accuracy'][b]['memory_off'], 'accuracy')} | "
                f"{fmt(pc['mttr_min'][b]['memory_on'], 'mttr_min')} | "
                f"{fmt(pc['mttr_min'][b]['memory_off'], 'mttr_min')} | "
                f"{fmt(pc['tool_calls'][b]['memory_on'], 'tool_calls')} | "
                f"{fmt(pc['tool_calls'][b]['memory_off'], 'tool_calls')} |"
            )
    w("")
    w("## Transfer to held-out machines")
    w("")
    tr = s["transfer"]
    w(
        "First exposure on M4–M5 after two exposures on M1–M3 — accuracy: memory ON "
        f"{fmt_rate(tr['memory_on'])}, memory OFF {fmt_rate(tr['memory_off'])}. "
        f"A same-class prior was among the matches in {tr['same_class_rank']['found']} of "
        f"{tr['same_class_rank']['n']} (mean rank "
        f"{'—' if tr['same_class_rank']['mean_rank'] is None else round(tr['same_class_rank']['mean_rank'], 2)})."
    )
    w("")
    w("## Discrimination")
    w("")
    for c in CONDS:
        d = s["discrimination"][c]
        w(
            f"- **{c}**: {d['probes']['k']} false replays in {d['probes']['n']} probes (incidents "
            f"where memory already held another class; rate {fmt_rate(d['rate'])}); sensor drift "
            f"right after a config regression: {d['sensor_after_config']['k']} / "
            f"{d['sensor_after_config']['n']}."
        )
    w("")
    w("## Retrieval quality (memory ON)")
    w("")
    r = s["retrieval"]
    w(f"- recall@1 when a same-class prior exists: {fmt_rate(r['recall_at_1'])}")
    w(f"- gate precision (matches of the same class): {fmt_rate(r['gate_precision'])}")
    w(f"- precision of matches labelled *strong*: {fmt_rate(r['strong_precision'])}")
    mw = r["matched_without_same_class_prior"]
    w(
        f"- incidents with no same-class prior that still got a match: {mw['k']} / {mw['n']} "
        f"(mean matches per incident {'—' if r['mean_matches'] is None else round(r['mean_matches'], 2)})"
    )
    w("")
    w("## Is confidence honest?")
    w("")
    for c in CONDS:
        cal = s["calibration"][c]
        b = "—" if cal["brier"] is None else f"{cal['brier']:.3f}"
        w(f"**{c}** — Brier score {b} (0 = perfect, 0.25 = always saying 50%)")
        w("")
        w("| Stated confidence | n | Mean stated | Observed accuracy |")
        w("|---|---|---|---|")
        for bin_ in cal["bins"]:
            ms = "—" if bin_["mean_confidence"] is None else f"{bin_['mean_confidence']:.0%}"
            acc = "—" if bin_["accuracy"] is None else f"{bin_['accuracy']:.0%}"
            w(f"| {bin_['lo']:.0%}–{bin_['hi']:.0%} | {bin_['n']} | {ms} | {acc} |")
        w("")
    w("![Calibration](charts/calibration.png)")
    w("")
    w("## Cost")
    w("")
    w(
        "| Condition | LLM tokens / incident | LLM $ / incident | Hindsight billed tokens (est.) | Hindsight $ (est.) | Runbook refreshes | Real s / incident |"
    )
    w("|---|---|---|---|---|---|---|")
    for c in CONDS:
        k = s["cost"][c]
        if k["llm_tokens"] is None:
            continue
        w(
            f"| {c} | {k['llm_tokens']:,.0f} | ${k['cost_usd']:.4f} | "
            f"{k['hindsight_billed_tokens']:,.0f} | ${k['hindsight_cost_usd']:.4f} | "
            f"{k['runbook_refreshes']:.2f} | {k['agent_time_real_s']:.1f} |"
        )
    t = s["totals"]
    w("")
    w(
        f"Total: {t['incidents']} incidents, {t['llm_tokens']:,} LLM tokens, "
        f"${t['llm_usd']:.2f} LLM + ${t['hindsight_usd']:.2f} Hindsight (estimated; the "
        "Hindsight billing page is authoritative)."
    )
    w("")
    w("## Where it fails")
    w("")
    if not s["failures"]:
        w("No incorrect first recommendations.")
    else:
        w(
            "| Class | Condition | Seed | # | Incident | Machine | Seen | Recommended | Top match | Trace |"
        )
        w("|---|---|---|---|---|---|---|---|---|---|")
        for f in s["failures"]:
            tm = f["top_match"]
            top = (
                "—"
                if tm is None
                else f"{tm['incident_id']} ({(tm['type'] or '?').replace('_', ' ')}, {tm['rerank']:.2f})"
            )
            rec = f["recommended"] or "—"
            if f["false_replay"]:
                rec += " ⚠ false replay"
            if f["error"]:
                rec += f" (error: {f['error']})"
            trace = f"[trace]({f['trace_url']})" if f["trace_url"] else "—"
            w(
                f"| {f['true_type'].replace('_', ' ')} | {f['condition']} | {f['seed']} | "
                f"{f['position']} | {f['incident_id']} | {f['machine_id']} | {f['exposure']} | "
                f"{rec} | {top} | {trace} |"
            )
    w("")
    w("## Per-seed accuracy")
    w("")
    w(
        "Incidents within a seed share a world and a memory bank, so they are not independent; "
        "pooled CIs above assume they are. Per-seed means show how much seeds differ."
    )
    w("")
    w("| Condition | " + " | ".join(f"seed {x}" for x in s["seeds"]) + " |")
    w("|---|" + "---|" * len(s["seeds"]))
    for c in CONDS:
        cells = [
            "—" if x not in s["per_seed_accuracy"][c] else f"{s['per_seed_accuracy'][c][x]:.0%}"
            for x in s["seeds"]
        ]
        w(f"| {c} | " + " | ".join(cells) + " |")
    w("")
    w("## Run metadata")
    w("")
    w("```json")
    w(json.dumps(meta, indent=2, default=str))
    w("```")
    w("")
    return "\n".join(out)


def charts(summary: dict[str, Any], out_dir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    colors = {"memory_on": "#a371f7", "memory_off": "#8b949e"}
    written: list[Path] = []

    def err(e: dict[str, Any] | None) -> tuple[float, float, float]:
        if e is None:
            return (float("nan"), 0.0, 0.0)
        return (e["mean"], e["mean"] - e["lo"], e["hi"] - e["mean"])

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    for ax, metric in zip(axes, ("accuracy", "mttr_min", "tool_calls"), strict=True):
        for offset, c in ((-0.12, "memory_on"), (0.12, "memory_off")):
            pts = [err(summary["by_exposure"][metric][b][c]) for b in BUCKETS]
            xs = [i + offset for i in range(len(BUCKETS))]
            ax.errorbar(
                xs,
                [p[0] for p in pts],
                yerr=[[p[1] for p in pts], [p[2] for p in pts]],
                fmt="o-",
                color=colors[c],
                capsize=4,
                label=c.replace("_", " "),
            )
        ax.set_xticks(range(len(BUCKETS)), BUCKETS)
        ax.set_xlabel("times this incident class had been seen")
        ax.set_title(LABEL[metric])
        if metric == "accuracy":
            ax.set_ylim(0, 1.05)
        else:
            ax.set_ylim(bottom=0)  # a zero baseline: differences are not visually exaggerated
        ax.grid(alpha=0.3)
        ax.legend()
    fig.suptitle("Learning curves by exposure: mean with 95% CI, memory ON vs OFF")
    fig.tight_layout()
    path = out_dir / "learning_curves.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    written.append(path)

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    for ax, metric in zip(axes, ("accuracy", "mttr_min", "tool_calls"), strict=True):
        pts = [err(summary["by_exposure"][metric][b]["diff"]) for b in BUCKETS]
        ax.bar(range(len(BUCKETS)), [p[0] for p in pts], color="#a371f7", alpha=0.8)
        ax.errorbar(
            range(len(BUCKETS)),
            [p[0] for p in pts],
            yerr=[[p[1] for p in pts], [p[2] for p in pts]],
            fmt="none",
            ecolor="black",
            capsize=5,
        )
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_xticks(range(len(BUCKETS)), BUCKETS)
        ax.set_title(f"{LABEL[metric]}: ON − OFF (paired)")
        ax.grid(alpha=0.3, axis="y")
    fig.suptitle("Effect of memory, paired by seed and position (95% CI)")
    fig.tight_layout()
    path = out_dir / "paired_differences.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    written.append(path)

    fig, ax = plt.subplots(figsize=(5.5, 5))
    ax.plot([0, 1], [0, 1], "--", color="black", linewidth=0.8, label="perfectly honest")
    for c in CONDS:
        bins = [b for b in summary["calibration"][c]["bins"] if b["n"]]
        ax.plot(
            [b["mean_confidence"] for b in bins],
            [b["accuracy"] for b in bins],
            "o-",
            color=colors[c],
            label=f"{c.replace('_', ' ')} (Brier "
            f"{summary['calibration'][c]['brier'] or float('nan'):.3f})",
        )
    ax.set_xlabel("stated confidence")
    ax.set_ylabel("observed accuracy")
    ax.set_xlim(0, 1.02)
    ax.set_ylim(0, 1.02)
    ax.grid(alpha=0.3)
    ax.legend()
    ax.set_title("Is confidence honest?")
    fig.tight_layout()
    path = out_dir / "calibration.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    written.append(path)
    return written


def write_report(
    out_dir: Path, rows: list[Row], meta: dict[str, Any], *, expected_units: int | None
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize(rows, expected_units=expected_units)
    charts(summary, out_dir / "charts")
    (out_dir / "results.json").write_text(
        json.dumps({"metadata": meta, "summary": summary, "rows": rows}, indent=1, default=str)
    )
    (out_dir / "REPORT.md").write_text(markdown(summary, meta))
    return summary


def group_by(rows: Iterable[Row], key: str) -> dict[Any, list[Row]]:
    out: dict[Any, list[Row]] = defaultdict(list)
    for r in rows:
        out[r[key]].append(r)
    return out
