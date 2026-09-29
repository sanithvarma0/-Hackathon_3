# Demo script (3–5 minutes)

**Live:** https://memoryops.vercel.app. The page is public and needs no login.

The backend runs the plant simulator continuously, so the page is live the moment it opens. The `memoryops-demo` memory bank already holds real history. Every episode in it came from a real agent run on this deployment (`scripts/seed_demo.py`, see [Seeded history](#seeded-history)); nothing was written by hand.

## Before you present

- Open https://memoryops-api.onrender.com/api/health. All four dependencies should read `ok`.
- Open the site at a width of 1440 px or more. Check the top bar:
  - **LIVE** is green.
  - **MEMORY** is ON.
  - **ALERTS** is 0.
- If an incident is still open from someone else, approve its recommendation, or wait. Only one incident runs at a time.
- Limits: 40 new incidents per hour, and a $10 cap on LLM spend. One incident costs about $0.01–0.05.

## The story in one line

> "Some incidents can't be fixed from the dashboards. Only the plant's engineers know the fix. MemoryOps escalates the first one, remembers what the engineer did, and fixes the next one itself."

## Beat 1: the plant, and memory's current state (30 s)

- **FLOOR:** show the five machines and two gateways with live throughput. Everything is simulated, with ground truth hidden from the agent.
- **◆ MEMORY tab:** show the episodes. Open a *vision dropout* episode that was **ESCALATED**, and read the on-call engineer's note aloud: the camera's PoE port on the gateway switch, fixed by a gateway restart. Point out that nothing in the metrics says "restart the gateway"; this note is the only place that knowledge exists.
- Point to **the runbook the agent wrote itself**. This is Hindsight's *Incident Patterns* mental model, which rewrites itself as incidents consolidate.

## Beat 2: memory ON, where the agent fixes what it once had to escalate (about 90 s)

Click **VISION DROPOUT** (under **◆ SITE**). Then narrate the right-hand trace as it streams:
1. **`recall_hints`** (purple): memory says which evidence was decisive last time.
2. **Tool calls**: machine metrics, error logs ("vision frame timeout on cam-…"), recent events.
3. **`recall_similar_incidents`** (purple): the agent asks memory mid-investigation.
4. **`search_memory` → matched incidents**: the earlier vision dropout episodes with their fix.
5. **Recommendation: RESTART_GATEWAY**, citing the past incident IDs, with high confidence.

Click **APPROVE**. The machine recovers.

Point at the result card, which shows time to recover (MTTR), tool calls, and "first recommendation correct". Compare with INC-002 in the Memory tab: the first vision dropout escalated at 36.4 sim-minutes, while INC-005 was fixed in 12.8. **SERVO DRIFT** tells the same story, citing INC-003 and INC-006.

## Beat 3: the same incident with memory OFF (optional, about 90 s)

1. Flip **MEMORY** to OFF in the top bar.
2. Click **VISION DROPOUT** again. The same evidence comes in, but there are no purple steps; the memory tool isn't even offered.
3. The agent can't know about the PoE port, so it escalates or guesses. Approve whatever it recommends.
4. Flip **MEMORY** back ON afterwards.

**Why this matters:** memory OFF still *retains* the episode. It only removes recall. That is what makes the ON vs OFF comparison fair.

## Beat 4: is it actually learning? (60 s)

Open the **LEARNING** tab → **EVAL REPORT** → **Site knowledge**. It shows 144 live incident runs, memory ON vs OFF, paired on identical incidents:

| | First recommendation correct | Tool calls | Time to recover |
|---|---|---|---|
| Site-knowledge incidents | **+46 pts** [+25, +67] | **−8.3** | **−15.3 sim-min** |
| Textbook incidents | +0 pts [−6, +6], no harm | −0.9 | −0.4 |

Be upfront about the red targets, which are reported rather than tuned away:
- **Target 5, retrieval recall@1 at 65%:** Hindsight Cloud's cross-encoder reranker went to passthrough mid-project, so an LLM judge gates matches instead.
- **Target 4, false replays:** 7 of 69.

**THIS SESSION** shows the live incidents from the demo deployment.

## If asked

- **"Is memory just a prompt dump?"** No. Hindsight extracts facts from each prose episode. Recall is semantic: queries are paraphrased from observed signals and never contain IDs or numbers, and matches are gated per past incident. The agent must cite the incident IDs it used, and the UI shows which ones.
- **"What stops it replaying the wrong fix?"** Matching is by *trigger and decisive evidence*, not symptoms, since different classes share symptoms. Actions that failed before are passed to the agent as *known to fail*. A human approves every action, and the verify step retries and retains a lesson on a bad fix.
- **"What if Hindsight is down?"** Every episode is written to SQLite first, and a retry loop retains it later. The incident still completes, and the UI shows the error.
- **"Where are the traces?"** Every incident has a Langfuse trace (the **trace ↗** link on the result card). Every LLM call and Hindsight operation is also in the spend ledger (`/api/usage`).

## Seeded history

The storyline below ran on this deployment before the demo, using `uv run python scripts/seed_demo.py --base https://memoryops-api.onrender.com`. Incidents are numbered in the order they ran.

| Incident | Class, machine | Memory held | Agent's recommendation(s) | Outcome |
|---|---|---|---|---|
| INC-001 | config regression, M4 | nothing | ROLLBACK_CONFIG (97%) | resolved, 8.4 sim-min (browser smoke test) |
| INC-002 | vision dropout, M3 | no vision history | ESCALATE_HUMAN (86%) | **escalated**, 36.4 sim-min. The engineer's note: *camera PoE port on the GW-B switch; restarting the gateway re-powers it* |
| INC-003 | servo drift, M5 | no servo history | ESCALATE_HUMAN (62%) | **escalated**, 38.8 sim-min. The engineer's note: *stale cached tuning table after the nightly firmware check; needs a cold restart* |
| INC-004 | sensor drift, M2 | INC-003 (servo) | RESTART_MACHINE (84%) → CLEAR_CACHE, citing INC-003 → RECALIBRATE_SENSOR | resolved on the **3rd attempt**, 36.3 sim-min. **Memory misled it**: see below |
| INC-005 | vision dropout, M4 | INC-002 | **RESTART_GATEWAY (78%), citing INC-002** | **resolved first time, 12.8 sim-min** (vs 36.4) |
| INC-006 | servo drift, M2 | INC-003 | **RESTART_MACHINE (93%), citing INC-003** | **resolved first time, 14.9 sim-min** (vs 38.8) |

Seeding INC-002 to INC-006 cost $0.09 in LLM calls and about $0.36 in Hindsight operations (estimated).

**INC-004 is kept on purpose.** Sensor drift arrived right after a servo incident on the same kind of symptoms, and the agent carried INC-003's "stale cache" idea over:
1. It tried a restart, then a cache clear, both of which the verify step rejected.
2. It then recalibrated the sensor.
3. Both failed fixes were retained as lessons (`kind:lesson`), so they now reach the agent as *known to fail* for this signature.

This is the false-replay weakness the eval measures (target 4, 7 of 69 probes). If a judge asks whether memory can hurt, open INC-004 in the Memory tab: yes, it can, and here is how the system contains it. A human approves every action, verify catches a bad fix, and the failure itself becomes memory.
