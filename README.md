# multi-agent-realstate

Two-runtime monorepo: Next.js 16 frontend + FastAPI/LangGraph backend with ARQ workers, Supabase Auth/Postgres, and Redis.

## Local Dev — Three Commands

```bash
# 1. Start Supabase local stack (Postgres :54322, Studio :54323, Auth :54321)
supabase start

# 2. Start backend API + worker + Redis
docker compose up

# 3. Start Next.js dev server
cd frontend && pnpm dev
```

Frontend runs at `http://localhost:3000`, backend at `http://localhost:8000`.

## Environment Setup

```bash
cp .env.example backend/.env
cp frontend/.env.local.example frontend/.env.local
# Edit both files with your Supabase project credentials
```

## Operational Notes

### SSE Buffering on Railway

Streaming endpoints (agent progress, scraping status) must include:

```
X-Accel-Buffering: no
```

Without this header Railway's proxy buffers SSE chunks and the client receives them in bursts. Add it in the FastAPI route response headers for any `StreamingResponse`.

### Apify Webhook Tunnel

In local dev Apify cannot reach `localhost`. Run a tunnel before triggering scraping jobs:

```bash
cloudflared tunnel --url http://localhost:8000
# or
ngrok http 8000
```

Set the tunnel URL as your Apify webhook endpoint.

### Vercel Deployment

When deploying to Vercel, set the **Root Directory** to `frontend/` in the project settings. Vercel must not try to build from the repo root.

### Railway Worker Service

The API and worker are two separate Railway services from the same repository. The `railway.toml` configures the API service. The worker service must be configured in the Railway dashboard with:

```
Start Command: arq app.workers.worker.WorkerSettings
```
