# Security

LLM keys stay on the API process. The Next.js bundle is public; never put a provider key in `NEXT_PUBLIC_*`.

## What belongs in git

| Commit | Keep out of git |
|--------|-----------------|
| `.env.example` with empty values | `.env`, `.env.local`, `.env.production` |
| Docs that name `XAI_API_KEY` | Real `xai-…` tokens |
| Deploy steps | Cloud dashboard secrets |

`.gitignore` already ignores `.env`, `.env.*` (except `*.env.example`), `lenswrite-backend/.env`, and `lenswrite-frontend/.env.local`.

## Where to set values

| Name | Local | Cloud |
|------|-------|-------|
| `XAI_API_KEY` | `lenswrite-backend/.env` | Railway / Render variables |
| `CORS_ORIGINS` | `lenswrite-backend/.env` | Railway / Render variables |
| `NEXT_PUBLIC_API_BASE` | `lenswrite-frontend/.env.local` | Vercel env (API URL only) |

`NEXT_PUBLIC_API_BASE` is the public FastAPI origin, not a model key.

## If a key leaked

1. Rotate it at [console.x.ai](https://console.x.ai)
2. Update the host variables and redeploy
3. Treat any key that was in git history or chat as burned
