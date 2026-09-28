# MemoryOps frontend — the control room

Next.js (App Router) + Tailwind v4 + Recharts + hand-rolled SVG. One screen: the plant floor, the
investigation trace, the memory panel (purple = Hindsight, nothing else) and the recommendation
card; plus the Memory Browser and Learning tabs. Spec: [BUILD_PLAN.md](../BUILD_PLAN.md) section 10.

```bash
npm install
npm run dev        # http://localhost:3000, expects the backend on NEXT_PUBLIC_API_BASE
npm test           # reducer tests (vitest)
npm run typecheck && npm run lint && npm run build
```

How state works (`lib/`):

- `reducer.ts` — one pure reducer turns the SSE event stream into everything on screen. The same
  reducer replays an incident's stored events after a reload, so a reloaded page and a page that
  watched live end up identical (tested in `reducer.test.ts`).
- `useMemoryOps.ts` — boot: snapshot → replay the active or latest incident → open the stream
  from the snapshot's `last_event_id`. Any disconnect repeats the boot; while the backend is down
  the page shows the designed offline state.
- Visual cues come from observable metrics only (temperature, sensor variance, memory, packet
  loss, config version), never from the incident class — random and custom incidents stay hidden
  until they close. Thresholds are taken from the simulator's ranges (`components/ui/status.ts`).
