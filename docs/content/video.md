# Video: 3 minutes, screen + voice

## Before you record

1. Open https://memoryops-api.onrender.com/api/health. Everything should say `ok`.
2. Open https://memoryops.vercel.app full screen at 1080p. The top bar should show **LIVE**, **MEMORY ON** and **ALERTS 0**.
3. Close notifications. Start Loom in "Screen + Camera" mode.

## Record: 5 steps

Speak in your own words. The lines below are a guide.

**1. Intro (20 s): your face, then the floor view**
> "Hi, I'm Sanith, a data scientist. This is MemoryOps: an AI agent that handles incidents on a simulated factory line, with Hindsight as its long-term memory. The question I wanted to answer: does memory actually make an agent better, and can I measure it?"

**2. The problem (30 s): open ◆ MEMORY, click INC-002**
> "Some faults can't be solved from the data alone. The first time this camera fault happened, the agent investigated, found nothing conclusive, and escalated to a human. The human's fix was saved to memory as an episode. That's the only place that knowledge exists."

**3. Live demo (90 s): FLOOR → click VISION DROPOUT → APPROVE when it asks**
> "Same fault, different machine. Watch the trace on the right. Purple is memory: it recalls hints first, then queries memory mid-investigation, then matches past incidents."
>
> "It recommends RESTART GATEWAY and cites the earlier incident. I approve, it verifies the machine recovered, and it saves this run back to memory. First time: escalated, 36 minutes. Now: fixed on the first try, 13 minutes."

The agent takes 1–2 minutes. Talk over the trace, or trim the wait afterwards.

**4. The results (30 s): LEARNING tab → Site knowledge**
> "One good run could be luck, so I tested it. I gave the agent the same incidents twice, once with memory and once without: 144 runs in total."
>
> "With memory, the agent picks the right fix first time 46 points more often, and it needs about half the tool calls. On everyday faults it performs the same either way, so memory never gets in its way."

**5. Takeaway (15 s): your face**
> "What surprised me: the gains came from what the agent writes to memory, not from tuning retrieval. Links are below. Thanks for watching."

## Upload to YouTube

- **Title:** `My AI agent escalated once. Then it fixed the repeat itself`
- **Description:**
  ```
  MemoryOps: an incident agent that learns from past resolutions using Hindsight agent memory.

  Article: https://dev.to/bunny0711/the-fix-wasnt-in-the-logs-hindsight-remembered-it-28b0
  Code: https://github.com/sanithvarma0/memoryops
  Live: https://memoryops.vercel.app
  Hindsight: https://github.com/vectorize-io/hindsight
  ```
- **Thumbnail:** make it with the prompt below, then upload it.
- **Audience:** "Not made for kids". **Visibility:** **Public**.

Other titles, if you prefer:
- Memory ON vs OFF: what agent memory actually changes (144 runs)
- The fix wasn't in the logs: giving an AI agent memory

## Thumbnail prompt (Gemini / Nano Banana)

Attach your photo, then paste:

```
Create a 16:9 YouTube thumbnail. Dark dashboard background with glowing machine cards. Left: a red label "ESCALATED · 36 min". Right: a green label "FIXED FIRST TRY · 13 min". A glowing purple arrow between them labelled "memory". Put the person from the attached photo on the right, looking at the green result. Big bold text at the top: "IT REMEMBERED". Purple and dark navy colours, high contrast, no small text.
```
