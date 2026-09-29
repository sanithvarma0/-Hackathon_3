"""Run the demo storyline against a deployed MemoryOps, so the demo bank holds real history.

Drives the public HTTP API as an operator would: trigger an incident, wait for the agent's
recommendation, approve it (retrying as the agent retries), until the incident resolves or is
escalated to the on-call engineer, whose fix and note are retained to memory. Nothing is
fabricated: every episode in the bank comes from a real agent run on the live stack.

  uv run python scripts/seed_demo.py --base https://memoryops-api.onrender.com
  uv run python scripts/seed_demo.py --base http://localhost:8000 --only vision_link_dropout
"""

import argparse
import sys
import time
from typing import Any

import httpx

# First occurrences of the site-knowledge classes escalate (the fix isn't in the signals); the
# engineer's note is what memory can teach. Textbook classes give memory something to *not*
# confuse them with.
STORYLINE = [
    "vision_link_dropout",
    "servo_tuning_drift",
    "sensor_drift",
    "vision_link_dropout",
    "servo_tuning_drift",
]


def wait_idle(http: httpx.Client) -> None:
    for _ in range(200):
        if http.get("/api/state").json()["active_incident"] is None:
            return
        time.sleep(3)
    sys.exit("an incident is still active; resolve it first")


def run_incident(http: httpx.Client, incident_type: str) -> dict[str, Any]:
    wait_idle(http)
    r = http.post("/api/incident/predefined", json={"type": incident_type})
    r.raise_for_status()
    incident_id = r.json()["id"]
    print(f"\n{incident_id} {incident_type} on {r.json()['machine_id']}", flush=True)
    approved: set[int] = set()
    deadline = time.time() + 900
    while time.time() < deadline:
        body = http.get(f"/api/incidents/{incident_id}").json()
        inc: dict[str, Any] = body["incident"]
        pending = body["pending"]
        if pending and inc["agent_status"] == "awaiting_action":
            rec = pending["recommendation"]
            attempt = int(pending.get("attempt", len(approved) + 1))
            if attempt not in approved:
                approved.add(attempt)
                cited = ", ".join(rec.get("cited_incidents") or []) or "none"
                print(
                    f"  attempt {attempt}: {rec['action']} ({rec['confidence']:.0%}) "
                    f"cites {cited} :: {rec['diagnosis'][:90]}",
                    flush=True,
                )
                http.post(
                    f"/api/incident/{incident_id}/action", json={"action": rec["action"]}
                ).raise_for_status()
        if inc["status"] in ("resolved", "escalated") and inc["agent_status"] in (
            "finished",
            "failed",
        ):
            retained = [m["retain_status"] for m in body["memory_records"]]
            if all(s != "pending" for s in retained):
                mttr = (inc["mttr_sim_s"] or 0) / 60
                print(
                    f"  -> {inc['status'].upper()} · MTTR {mttr:.1f} sim-min · memory {retained}",
                    flush=True,
                )
                if inc.get("engineer_note"):
                    print(f"  engineer: {inc['engineer_note'][:160]}", flush=True)
                return inc
        time.sleep(3)
    sys.exit(f"{incident_id} did not finish in 15 minutes")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", required=True, help="backend URL")
    ap.add_argument("--only", help="run one incident of this type instead of the storyline")
    ap.add_argument(
        "--settle", type=float, default=45, help="seconds to let Hindsight index between runs"
    )
    args = ap.parse_args()
    plan = [args.only] if args.only else STORYLINE
    with httpx.Client(base_url=args.base.rstrip("/"), timeout=60) as http:
        print("health:", http.get("/api/health").json()["ok"], "· bank", end=" ")
        print(http.get("/api/state").json()["bank_id"])
        for i, incident_type in enumerate(plan):
            run_incident(http, incident_type)
            if i < len(plan) - 1:
                time.sleep(args.settle)
        session = http.get("/api/usage").json()["session"]
        print(
            f"\nspend this deployment: LLM ${session['cost_usd']:.3f} + "
            f"Hindsight ~${session['memory_cost_usd']:.3f}"
        )


if __name__ == "__main__":
    main()
