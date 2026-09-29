# The fix wasn't in the logs. Hindsight remembered it.

The first time the vision camera on a robotic welding cell started dropping frames, my incident agent did the right thing: it gave up. It had called eight tools, ruled out a config deploy, a gateway outage and a stale calibration, and escalated to a human at 86% confidence. The on-call engineer found the cause in a few minutes. The camera's PoE port on the gateway switch sags under load, which is a known quirk of that line, and restarting the gateway re-powers it.

Nothing in the telemetry says "restart the gateway". The only place that fix existed was a sentence in the engineer's note on the ticket.

The next time it happened, on a different machine, the agent recommended `RESTART_GATEWAY` on its first attempt and cited the earlier incident by ID. Recovery took 12.8 simulated minutes instead of 36.4. What changed in between was one retained episode in [Hindsight agent memory](https://github.com/vectorize-io/hindsight).

This post is about building that loop, and measuring it honestly.

## What MemoryOps does

MemoryOps is an incident commander for a production line.

**The plant.** A simulator models five machines behind two network gateways. It injects faults and keeps the ground truth hidden from the agent. The same simulator scores every recommendation, which is what makes the results in this post measurable.

**The agent.** A single LangGraph agent works each incident through these steps:

1. **Detect** the alert.
2. **Recall hints** from memory.
3. **Investigate** with four evidence tools: machine metrics, metric history, error logs and recent events.
4. **Search memory** for past incidents with the same signature.
5. **Decide** on a recommendation.
6. **Stop and wait** for a human to approve the action.
7. **Act, then verify** that the machine actually recovered.

Every step streams to a Next.js control room over SSE, so the operator watches the investigation happen, not just the verdict.

![Architecture: FastAPI service with the simulator, LangGraph agent and SQLite; Hindsight for retain and recall](https://raw.githubusercontent.com/sanithvarma0/memoryops/main/docs/content/img/architecture.png)

**Two kinds of incident.** I split the incidents into two families, and that split turned out to be the most important design decision in the project:
- **Textbook incidents.** The fix follows from the evidence. A config deploy 12 minutes before onset means roll back the config. A good LLM with good tools gets these right without any memory.
- **Site-knowledge incidents.** The fix cannot be derived from the signals. It is known only to the people who run the plant. The vision dropout above is one; a servo tuning table that goes stale after a nightly firmware check and only reloads on a cold restart is another.

A stateless agent will never solve the second family, however good the model is. The best it can do is escalate. Memory is the only way in.

## Hindsight is the only long-term memory

I didn't want memory to be "stuff the last N incidents into the prompt". [Hindsight](https://hindsight.vectorize.io/) takes free text on write, extracts facts from it, and on recall gives back ranked facts, consolidated observations, and a mental model it keeps rewriting as memories consolidate. So the design question became: what exactly should the agent write?

**The episode record.** When an incident closes, the agent writes one prose episode. Its most important line is a generalized **signature** with no machine IDs or numbers. Next to it go:
- the evidence that actually decided the case
- every action tried, and its effect
- on an escalation, the engineer's note

The retain call itself is small:

```python
await self._client.aretain(
    bank_id=self.bank_id,
    content=record.text,
    context="production incident resolution",
    timestamp=record.timestamp,
    document_id=record.document_id,   # the incident ID: idempotent upsert
    metadata=record.metadata,          # diagnosis, final action, signature, decisive evidence
    tags=[f"kind:{record.kind}"],     # "episode" or "lesson"
    retain_async=False,                # recallable by the very next incident
)
```

The engineer's note goes into the `RESOLUTION` line of the episode, which is where the site knowledge lives:

```python
f"RESOLUTION: final action {ep.attempts[-1].action if ep.attempts else 'none'}, "
f"MTTR {ep.mttr_sim_s / 60:.1f} sim-minutes."
+ (f" {ep.engineer_note}" if ep.engineer_note else ""),
f"OUTCOME: {ep.outcome}.",
f"LESSON: {lesson_line(ep)}",
```

**Failed fixes.** When the verify step catches a fix that did nothing, the agent retains a separate **lesson** immediately, before the incident is even over. The lesson reads like "Do not rely on RESTART_MACHINE for this signature". Failure becomes memory even if nobody ever closes the ticket.

**Three reads.** The agent reads from memory at three points:
1. **Before investigating** (`recall_hints`): it recalls with a query built from the alert alone. This tells it which evidence was decisive last time, so it calls fewer tools.
2. **Mid-investigation:** it can call `recall_similar_incidents` as a tool. That tool is only offered when memory is on.
3. **Before deciding** (`search_memory`): matches are grouped per past incident and gated. The decide step must cite the incident IDs it relied on. A guardrail rejects any citation that memory didn't actually return, and I have watched it fire in live traces when the model tried to cite an incident from its hints.

![Memory browser: INC-002's escalated episode with the engineer's note, and the observations Hindsight consolidated](https://raw.githubusercontent.com/sanithvarma0/memoryops/main/docs/content/img/memory-engineer-note.png)

**Match on the trigger, not the symptoms.** My first version matched past incidents by symptoms, and it was wrong in an instructive way. Half the classes look identical from a dashboard: throughput down, errors up, some motion warnings. The agent kept "remembering" a servo incident when it was looking at sensor drift. What separates classes is the **trigger and the identifying evidence**: what changed just before onset, and what the investigation found. That is what the signature line and the match query describe now.

## The day my memory silently turned off

Hindsight scores recall results with a cross-encoder reranker, and my match gate was built on that score. A strong match needs a reranker score well above the others.

Then a full evaluation run came back with zero memory hits across 144 incidents. The agent hadn't gotten worse. Memory had simply stopped matching, and nothing had errored.

The cause was upstream. The cloud deployment had switched to a passthrough reranker: `scores.reranker` came back `null`, and `final` encoded only rank position. My gate rejected every candidate, and memory-ON became memory-OFF without a single exception. I kept that run in the repo, labelled invalid. It turned out to be a useful noise floor.

The fix had two parts:
- **Stop assuming the scores exist.** Hindsight's rank order is still meaningful, so when there are no reranker scores, the agent's LLM rates the top five candidates on the same question a cross-encoder answers: same trigger, same identifying evidence?
- **Never fail silently again.** Every match now records which gate produced it: `reranker`, `llm_judge` or `rank_only`. The eval report prints `MEMORY NEVER MATCHED` whenever memory-ON incidents that had same-class history never matched.

```python
if scored or not facts:
    matches = apply_match_rule(facts, rel=self._rel, floor=self._floor,
                               exclude_incident=exclude_incident)
else:  # passthrough reranker: judge the top-ranked candidates instead
    matches, gate = await self._fallback_gate(query, facts, exclude_incident)
```

The same thing happened later with the mental model. Refreshes completed, but the content stayed at "Generating content...". So the runbook panel now falls back to the observations Hindsight had consolidated, and says so, and the placeholder never reaches a prompt. Both are dependencies I don't control, so I now plan for them to degrade.

## Results: memory ON vs OFF, paired

**The evaluation.** The same agent ran the same incident sequences twice: once with memory on and once with it off. That was 3 seeds × 24 incidents per condition, 144 live runs on the real LLM and Hindsight, with a fresh memory bank for every condition and seed.

**Memory OFF still retains.** It only removes recall and the memory tool. So the only difference between the two runs is whether the agent can *use* what it wrote, which makes the comparison fair.

| Incident family | First recommendation correct (ON − OFF) | Tool calls | Time to recover |
|---|---|---|---|
| Site knowledge | **+46 points** [+25, +67] | **−8.3** | **−15.3 sim-min** |
| Textbook | +0 points [−6, +6] | −0.9 | −0.4 sim-min |

The brackets are paired 95% bootstrap confidence intervals.

**What the numbers say:**
- **Site knowledge.** On incidents the agent had seen before, memory-ON picked the right fix 75–83% of the time. Memory-OFF managed 0–33%, and that was mostly lucky guesses.
- **Textbook.** Memory didn't raise accuracy, which was already near 100%, but it didn't lower it either. It cut tool calls, because the hints tell the agent where to look first.

![Eval report: site-knowledge incidents, memory ON vs OFF by exposure with 95% CIs](https://raw.githubusercontent.com/sanithvarma0/memoryops/main/docs/content/img/eval-site-knowledge.png)

**A single-incident contrast.** I also ran the same vision dropout on the deployed system with memory off. The agent guessed `RESTART_MACHINE`, which had no effect, then tried `RESTART_GATEWAY`: 19 tool calls and two attempts. With memory on, it was 8 tool calls and one attempt.

**The targets I missed.** Three of my six acceptance targets fail, and they're in the report next to the ones that pass:
- **Retrieval recall@1 is 65%**, against a target of 80%. Part of that is the LLM-judge fallback.
- **The agent replayed another class's fix in 7 of 69 probes.** I watched one happen live. A sensor drift incident arrived right after a servo incident, and the agent borrowed the servo diagnosis for two attempts before recalibrating. The two failed fixes were retained as lessons, and they now come back as "known to fail" for that signature.

## What I'd tell someone adding memory to an agent

1. **Split your evaluation by whether memory *can* help.** Averaged together, my results said "+14%, CI touches zero". Split by family, they said "+46 points where knowledge is local, no harm where it isn't". If every test case can be solved from first principles, you're measuring the model, not the memory.
2. **Design the record before the recall.** The biggest accuracy wins came from what I wrote: a signature with no IDs, the decisive evidence, and the human's note. They didn't come from tuning thresholds.
3. **Retain failures, not just resolutions.** A lesson written the moment a fix fails is the cheapest guard against replaying it.
4. **Make "memory did nothing" loud.** A memory layer that returns zero matches looks exactly like a memory layer that isn't needed. Record which gate made every match, and alarm when memory never matches.
5. **Keep the human in the loop and the citations honest.** Every action waits for approval, and the agent can only cite incidents memory actually returned. That's what makes it safe to let a memory-driven recommendation near a production line.

The code is at [github.com/sanithvarma0/memoryops](https://github.com/sanithvarma0/memoryops). The full evaluation report, with the failures, is under `docs/eval/`. If you're thinking about long-term memory for an agent, Vectorize's explainer on [what agent memory is](https://vectorize.io/what-is-agent-memory) is a good starting point, and the [Hindsight docs](https://hindsight.vectorize.io/) cover retain, recall and mental models in detail.
