# Deploy

Split the stack: API and UI are separate services.

| Service | Host | Root directory |
|---------|------|----------------|
| API | [Railway](https://railway.app) or [Render](https://render.com) | `lenswrite-backend/` |
| UI | [Vercel](https://vercel.com) | `lenswrite-frontend/` |

```
Browser  →  Vercel (Next.js)  →  NEXT_PUBLIC_API_BASE  →  Railway (FastAPI)  →  xAI
```

The frontend does not hold `XAI_API_KEY`.

## 1. API (Railway)

1. New Project → Deploy from GitHub → this repo
2. **Settings → Root Directory** = `lenswrite-backend`
3. Builder: **Dockerfile** (avoid Nixpacks at the monorepo root; it can pick up the Next app)
4. Variables:

   | Key | Value |
   |-----|--------|
   | `XAI_API_KEY` | your xAI key |
   | `USE_HTTP_PROXY` | `0` |
   | `CORS_ORIGINS` | placeholder until the Vercel URL exists |
   | `PYTHONUNBUFFERED` | `1` |

5. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
6. Generate a public domain, then open `https://<api-host>/health`  
   Expect `"status": "ok"` and `"has_xai_key": true`

### Render

Root Directory `lenswrite-backend`. Build `pip install -r requirements.txt`. Start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Same variables as Railway.

### Docker

```bash
cd lenswrite-backend
docker build -t lenswrite-api .
docker run -p 8000:8000 \
  -e XAI_API_KEY=... \
  -e USE_HTTP_PROXY=0 \
  -e CORS_ORIGINS=https://your-app.vercel.app \
  lenswrite-api
```

## 2. UI (Vercel)

1. Import the GitHub repo
2. **Root Directory** = `lenswrite-frontend` (required; a root of `.` 404s)
3. Framework: Next.js
4. Environment:

   | Key | Value |
   |-----|--------|
   | `NEXT_PUBLIC_API_BASE` | API origin, no trailing slash |

5. Deploy and copy the `*.vercel.app` URL

If the site 404s, Root Directory is still the monorepo root. Set it to `lenswrite-frontend` and redeploy.

## 3. CORS

On the API service:

```text
CORS_ORIGINS=https://your-app.vercel.app
```

Exact browser origin: `https`, no trailing slash. Multiple origins are comma-separated. Optional: `CORS_ORIGIN_REGEX=1` to allow `*.vercel.app` previews. Redeploy the API after changing CORS.

## 4. Check

1. `GET /health` → `ok`, `has_xai_key: true`
2. Open the Vercel app → fill a premise → Generate
3. Network tab: requests go to `NEXT_PUBLIC_API_BASE`
4. Wait 1–5 minutes for the draft (LLM latency is expected)

## Environment

### API

| Variable | Required | Notes |
|----------|----------|-------|
| `XAI_API_KEY` | yes | generation fails without it |
| `CORS_ORIGINS` | production | frontend origin(s) |
| `USE_HTTP_PROXY` | production `0` | do not point at a local VPN |
| `XAI_MODEL` | no | default `grok-3` |

### UI

| Variable | Required | Notes |
|----------|----------|-------|
| `NEXT_PUBLIC_API_BASE` | yes | API origin, no trailing `/` |

Generation can exceed free-tier HTTP timeouts. The UI already polls `/status`; keep the API process alive for background graph runs.
