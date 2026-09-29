# Video (guide Part 3): script, titles, thumbnail

**Length:** about 3:30. **Format:** screen recording with voiceover, plus your face in a corner if you can (the guide prefers it). **Resolution:** 1080p or higher.

**Record at:** https://memoryops.vercel.app in a 1920×1080 browser window, zoomed to 100–110%.

**Before recording:**
- Close notifications and other tabs.
- Check that **MEMORY** shows ON and **ALERTS** shows 0.
- Open https://memoryops-api.onrender.com/api/health once, so the backend is warm.
- The agent needs 1–3 minutes of real time per incident. Record in one take and cut the waiting in editing, or talk over it (the trace keeps moving).

The narration below is a guide, not a teleprompter. Say it your way.

---

## 0:00–0:30 · Intro

**On screen:** your face (webcam), then the MemoryOps floor view with the machines ticking.

> "Hi, I'm Sanith. This is MemoryOps, an incident agent for a production line. It investigates a fault, recommends a fix, and waits for a human to approve it. The interesting part is what happens with problems it can't solve from the data. I'll show you how it learns them from the engineers, using Hindsight as its memory."

## 0:30–1:00 · The problem: the fix isn't in the telemetry

**On screen:** open **◆ MEMORY** and click **INC-002** to expand the episode. Scroll to the RESOLUTION line with the engineer's note.

> "This was the first vision dropout the agent ever saw. It checked the metrics, logs and recent events, ruled out a config change and a network outage, and it escalated. That's the right call, because there's nothing in the signals that says what's wrong. The on-call engineer found it: the camera's power-over-ethernet port on the gateway switch sags under load, and restarting the gateway fixes it. That sentence only exists in the ticket. A stateless agent will escalate this forever."

**Optional contrast:** flip **MEMORY** OFF in the top bar and say:

> "With memory off, the same incident costs about twice the tool calls and a wrong first fix."

Then flip it back ON. Don't run a memory-OFF incident on camera; it takes too long.

## 1:00–2:40 · Live demo: recall, recommend, retain

**On screen:** **FLOOR** tab. Click **VISION DROPOUT** under **◆ SITE**. Narrate as the right-hand trace streams.

> "Now I trigger the same class of fault on a different machine."

When the purple **recall_hints** line appears:

> "Before it looks at anything, it asks Hindsight what mattered in similar incidents. These purple lines are memory: facts Hindsight extracted from past episodes."

During the tool calls:

> "Then it investigates with real tools: metrics, error logs, recent events. Here's `recall_similar_incidents`: the agent asking memory mid-investigation, as a tool call."

At **search_memory** and the recommendation card:

> "Before it decides, it searches memory for incidents with the same signature and has to cite what it used. There's a guardrail: it can't cite anything memory didn't return. And the recommendation is RESTART GATEWAY. It never saw that fix in the data; it learned it from the engineer's note."

Click **APPROVE · RESTART GATEWAY**. Wait for verify, then point at the green **RESOLVED** card and the purple toast.

> "It verifies the machine actually recovered, and then it retains this incident as a new episode. So the next one recalls it too. The first time, this took 36 simulated minutes and a human. Now it's about 13 minutes and one click."

Open **◆ MEMORY** again and point at the right-hand panel.

> "On the right is what Hindsight consolidated across incidents: observations like 'restart machine is ineffective for stale servo tuning tables'. Nobody wrote these."

## 2:40–3:10 · Does it actually learn? The numbers

**On screen:** **LEARNING** tab → **EVAL REPORT** → **Site knowledge**.

> "I didn't want to eyeball this, so I ran the same incident sequences with memory on and off, paired, 144 runs. On incidents where the fix is local knowledge, memory on gets the first recommendation right 46 points more often, with half the tool calls. On textbook incidents it does no harm. And three of my six targets fail. Retrieval is at 65%, and it sometimes replays the wrong class's fix. That's all in the report."

## 3:10–3:30 · Takeaway

**On screen:** your face, or the architecture diagram (`docs/content/img/architecture.png`).

> "What surprised me: the wins didn't come from tuning retrieval. They came from deciding what to write: one episode per incident, with a generalized signature, the evidence that decided it, and the human's note. And retaining the failures, not just the fixes. Code and the article are linked below. Thanks for watching."

---

## Five YouTube titles

1. My AI agent escalated once. Then it fixed the repeat itself
2. The fix wasn't in the logs: giving an incident agent memory
3. Memory ON vs OFF: what agent memory actually changes (144 runs)
4. I taught an AI agent fixes only engineers knew, with Hindsight
5. Building a self-learning incident agent with Hindsight and LangGraph

Recommended: **#1**. Put this in the description:

```
MemoryOps: an incident agent for a production line that learns fixes from past resolutions using Hindsight agent memory.

Article: https://dev.to/bunny0711/the-fix-wasnt-in-the-logs-hindsight-remembered-it-28b0
Code: https://github.com/sanithvarma0/memoryops
Live: https://memoryops.vercel.app
Hindsight: https://github.com/vectorize-io/hindsight
```

## Thumbnail prompt (Google Nano Banana)

Attach a photo of yourself (and teammates, if any) and use:

```
Generate a viral thumbnail for this YouTube video. Make the thumbnail attention grabbing and something that people scrolling would want to click on if they see it. The aspect ratio needs to be 16:9.

Video: "My AI agent escalated once. Then it fixed the repeat itself". A dark factory control-room dashboard with glowing machine cards, one machine in amber alert. On the left, a big red label "ESCALATED · 36 min"; on the right, a big green label "FIXED FIRST TRY · 13 min", with a glowing purple arrow between them labelled "memory". Place the attached person on the right third looking surprised at the green result. Bold, high-contrast text at the top: "IT REMEMBERED". Purple and dark-navy colour palette, no small text, no logos.

Here is the video script: [PASTE THE SCRIPT ABOVE]
```

Post the video to **YouTube** as **Public**, with this thumbnail. The guide rejects Drive links.
