# Deploying MemoryOps

The deployment has two parts:
- **Backend** (FastAPI, plant simulator, agent) runs on **Render** as one always-on Docker container. It has a persistent disk.
- **Frontend** (Next.js) runs on **Vercel**.

The backend can't be serverless. The simulator ticks continuously, the UI holds a long-lived SSE stream, and the incident log and event bus live in SQLite on a disk.

```
browser ──HTTPS──▶ Vercel (Next.js UI)
   │
   └──fetch + EventSource──▶ Render (Docker: uvicorn backend.main:app, disk /data)
                                  ├─▶ OpenAI (primary) / Groq (fallback)
                                  ├─▶ Hindsight Cloud (memory bank)
                                  └─▶ Langfuse (traces)
```

## Protecting spend on a public URL

| Env var | Effect |
|---|---|
| `DEMO_PASSCODE` | If set, every `POST /api/*` (trigger, approve, ignore, reset) needs the header `X-Demo-Passcode`. Reading the plant, the stream, memory and the eval report stays open. The UI asks for the passcode once and keeps it in the browser. |
| `TRIGGER_LIMIT_PER_HOUR` | Caps the number of new incidents in any rolling hour (default 40). Beyond the cap, requests get `429 RATE_LIMITED`. |
| `LLM_SPEND_CAP_USD` | Hard cap on LLM spend, tracked in the usage ledger on the disk. |

## 1. Backend on Render

1. Go to Render → **New** → **Blueprint**, and pick this repo. Choose the branch that contains `render.yaml`.
2. Render reads `render.yaml`, which creates one web service:
   - name `memoryops-api`
   - Docker runtime, *Starter* plan (persistent disks need a paid instance)
   - 1 GB disk at `/data`
   - health check on `/api/state`
3. Fill in the secret values Render asks for:
   - `HINDSIGHT_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`
   - `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`
   - `DEMO_PASSCODE`: any phrase
   - `CORS_ORIGINS`: `["http://localhost:3000"]` for now; step 3 updates it
4. Deploy. Check that `https://<service>.onrender.com/api/health` reports every dependency as `ok`.

The Blueprint uses the Hindsight banks `memoryops-demo` / `memoryops-demo-seeded`, which are separate from development and eval banks.

## 2. Frontend on Vercel

1. Go to Vercel → **Add New** → **Project**, and import this repo.
2. Set **Root Directory** to `frontend`. The framework is detected as Next.js.
3. Add the environment variable `NEXT_PUBLIC_API_BASE` = `https://<service>.onrender.com` (no trailing slash). It is inlined at build time, so redeploy after changing it.
4. Under **Settings** → **Git**, set the production branch to the deployment branch, then deploy.

## 3. Connect them

On Render, set `CORS_ORIGINS` to the Vercel URL as a JSON list, for example `["https://memoryops.vercel.app"]`. The service redeploys automatically.

## Local / fallback: Docker Compose

```bash
cp .env.example .env   # fill in keys
docker compose up --build
# UI http://localhost:3000 · API http://localhost:8000/docs
```

State is kept in the `memoryops-data` volume.

Behind a TLS-intercepting proxy, pass the proxy CA to the backend build:

```bash
docker build --secret id=ca,src=/path/ca.crt .
```
