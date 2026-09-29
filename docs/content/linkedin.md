# LinkedIn post (Prompt 3)

Post the text below as a **post**, not an article. It's 798 characters, under the prompt's limit of 800. Before posting, add a tag for **Code.in** as the guide asks: type `@Code.in` on the "Code:" line and pick the page.

---

Your agent can't learn a fix that isn't in the data.

Some fixes live only in an engineer's ticket note.

I built an incident agent for a production line. First time a camera link dropped, it escalated. Correctly.

The engineer's note ("PoE port sags, restart the gateway") went into Hindsight agent memory.

Next time: right fix, first try, citing the old ticket. 36 sim-min → 13.

What I'd copy:

→ Write one prose episode per incident: signature, decisive evidence, human note
→ Match on the trigger, not the symptoms
→ Retain failed fixes as lessons immediately
→ Evaluate memory ON vs OFF, paired
→ Alarm if memory never matches

Paired eval, 144 runs: +46 pts where fixes are local knowledge, no harm elsewhere.

Code: github.com/sanithvarma0/memoryops

#AIAgents #AgentMemory #Hindsight #LLM

---

## Media: attach the clip, not an image

Attach `img/linkedin-clip.mp4`: 35 s, 1080p, captioned, no audio needed. LinkedIn autoplays native video muted in the feed, so the captions carry the story. It's a real run on the live deployment (INC-010): the memory tab with INC-002's escalation, then a new vision dropout, memory matches INC-002/INC-005, the agent recommends RESTART GATEWAY citing INC-002, a human approves, and the recovery is verified. `img/linkedin-clip.gif` (14.7 MB) is a fallback only; the MP4 looks much sharper.

## First comment: the article

> I wrote up the full build, including the day my memory layer silently returned zero matches: https://dev.to/bunny0711/the-fix-wasnt-in-the-logs-hindsight-remembered-it-28b0

## Second comment: Hindsight (required by the guide)

> Here's Hindsight if you want to try agent memory yourself: https://github.com/vectorize-io/hindsight
