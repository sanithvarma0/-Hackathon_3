# Reddit submission (guide Part 1, step 5)

The guide asks for a **Link post** pointing at the published article, in one of these subreddits:
- r/LLMDevs
- r/SideProject
- r/AI_Agents
- r/aimemory

Pick one. Recommended: **r/LLMDevs**. It's the most technical audience and the best fit for the eval story. Read the subreddit's rules first; some ask for a flair or limit self-promotion.

## Post

**Type:** Link

**URL:** https://dev.to/bunny0711/the-fix-wasnt-in-the-logs-hindsight-remembered-it-28b0

**Title** (pick one):
- The fix wasn't in the logs. Hindsight remembered it.
- My incident agent escalated once, then fixed the repeat from memory. Paired ON/OFF eval inside
- I measured agent memory with paired ON/OFF runs: +46 pts on site-specific fixes, no harm elsewhere

**Flair:** "Discussion" or "Resource" on r/LLMDevs, "Showcase" on r/SideProject. Use whichever the subreddit offers.

## First comment (post right after submitting)

Reddit reacts badly to link drops with no context, so add this as a comment on your own post:

> Author here. Short version of what's in the post:
>
> - An incident agent for a production line: LangGraph, 4 evidence tools, and a human approves every action. [Hindsight](https://github.com/vectorize-io/hindsight) is its only long-term memory.
> - Some faults can't be fixed from telemetry. The fix is only in an engineer's ticket note. The agent escalates the first one, retains the note, and fixes the repeat itself: 36 → 13 simulated minutes to recover, citing the old ticket.
> - Evaluation: the same incident sequences with memory ON vs OFF, 3 seeds, 144 live runs. Result: +46 points first-fix accuracy on site-knowledge incidents [95% CI +25, +67], and +0 [−6, +6] on textbook ones. Memory OFF still writes, so the comparison is fair.
> - What went wrong:
>   - Retrieval recall@1 is 65%.
>   - It replayed another class's fix in 7 of 69 probes.
>   - One full eval run had zero memory matches because the hosted reranker went passthrough and my gate silently rejected everything. That run is in the repo, labelled invalid.
>
> Code and the full eval report: https://github.com/sanithvarma0/memoryops
>
> Happy to answer questions about the episode format or the eval design.
