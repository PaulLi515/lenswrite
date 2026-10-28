# LensWrite

Short-story generation: premise → internal pipeline → finished draft.

## Layout

```
lenswrite-backend/    FastAPI + LangGraph API
lenswrite-frontend/   Next.js UI
```

## Secrets

Do not commit API keys.

| File | Purpose |
|------|---------|
| `lenswrite-backend/.env.example` | Backend placeholders (`XAI_API_KEY`, optional `MOONSHOT_API_KEY`, `CORS_ORIGINS`) |
| `lenswrite-frontend/.env.example` | Frontend placeholder (`NEXT_PUBLIC_API_BASE`) |

Local copies:

```bash
cp lenswrite-backend/.env.example lenswrite-backend/.env
cp lenswrite-frontend/.env.example lenswrite-frontend/.env.local
```

Put real keys only in those local files, or in Railway / Vercel environment variables. See [SECURITY.md](./SECURITY.md).

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r lenswrite-backend/requirements.txt

cd lenswrite-frontend && npm install && cd ..

./start-backend.sh    # http://127.0.0.1:8000/docs
./start-frontend.sh   # http://localhost:3000
```

## Deploy

Split deploy is documented in [DEPLOY.md](./DEPLOY.md):

- API → Railway / Render (root = `lenswrite-backend`)
- UI → Vercel (root = `lenswrite-frontend`)
