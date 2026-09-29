# Content deliverables

Everything the content guide asks for, drafted from this repo and the live deployment. Every number comes from the committed eval run (`docs/eval/20260929-062025-06ac955`) or from the live incident records (INC-001 to INC-009).

| Deliverable | File | Who |
|---|---|---|
| Article title options (Prompt 1) | [titles.md](titles.md) | each member |
| Article, about 1,550 words plus code (Prompt 2) | [article.md](article.md) | each member (see "Teams" below) |
| Screenshots and diagram | [img/](img/) | shared |
| LinkedIn post plus the two comments (Prompt 3) | [linkedin.md](linkedin.md) | each member |
| Reddit link post plus first comment | [reddit.md](reddit.md) | once is enough |
| Video script, 5 titles, thumbnail prompt (Prompt 5/6) | [video.md](video.md) | one per team |

## Step 0: repo renamed ✓

The repo is now `github.com/sanithvarma0/memoryops`. The live site, the API and the article images were all checked after the rename.

## Order of operations

1. The article's images load from `raw.githubusercontent.com/sanithvarma0/memoryops/main/docs/content/img/`, which was checked after the rename.
2. **Publish the article** on Dev.to, Hashnode or Medium, as a public post.
   - **Dev.to / Hashnode:** paste `article.md` as-is. The markdown and image URLs work directly.
   - **Medium:** paste it, then check that the code blocks and images came through. Re-upload the images from `img/` if they didn't.
   - Add your own voice: a sentence about why you built it, or what was painful.
   - Tag Code.in where the platform allows.
3. **LinkedIn:** post [linkedin.md](linkedin.md). The first comment is the article URL; the second is the Hindsight repo link. Tag Code.in.
4. **Reddit:** a link post to the article, plus the first comment from [reddit.md](reddit.md).
5. **Video:** record with [video.md](video.md), make the thumbnail in Nano Banana, and upload to YouTube as Public.

## Pre-submit checklist (from the guide)

- [x] Title is about the idea or the result, not the hackathon.
- [x] Opens with something specific and surprising: the first vision dropout, escalated at 86% confidence.
- [x] Problem explained in concrete terms: a fix that exists only in an engineer's ticket note.
- [x] Shows where and how Hindsight is integrated: the retain call, the episode record, three recall points, the fallback gate.
- [x] At least one real code snippet: three, from `backend/memory/hindsight.py` and `backend/memory/render.py`.
- [x] At least one concrete before/after example:
  - INC-002 escalated after 36.4 sim-min; INC-005 was fixed first time in 12.8.
  - Memory OFF: 19 tool calls and 2 attempts. Memory ON: 8 tool calls and 1 attempt.
- [x] At least one honest lesson, limitation or dead end: the silent zero-match run, recall@1 at 65%, 7 of 69 false replays, and the INC-004 replay.
- [x] Screenshots and images included: the architecture diagram, the memory browser, and the eval chart.
- [x] Published to a public, linkable URL: https://dev.to/bunny0711/the-fix-wasnt-in-the-logs-hindsight-remembered-it-28b0
- [x] The word "hackathon" appears nowhere, including the repo URL and hashtags.
- [x] LinkedIn: https://www.linkedin.com/feed/update/urn:li:activity:7510646908323921920/ (repo link in the post, Code.in tagged, video attached, article URL and Hindsight repo as comments)
- [x] Reddit: https://www.reddit.com/r/SideProject/comments/1wt6stk/my_incident_agent_escalated_once_then_fixed_the/ (link post in r/SideProject)
- [x] Video: https://youtu.be/g4Yi_GZhfNI (3:59, YouTube)

## Teams

Each member submits **their own** article and LinkedIn post. The guide allows covering the same project from different angles. If there are several of you:
- keep `article.md` for one person
- give each other person a different angle from [titles.md](titles.md), for example:
  - "Hindsight's reranker went passthrough and my agent stopped remembering"
  - "How I evaluated agent memory without fooling myself"
- ask me to draft those articles too
