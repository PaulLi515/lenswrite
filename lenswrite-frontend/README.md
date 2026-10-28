# LensWrite UI

Next.js app for LensWrite. Setup, env vars, and deploy live in the [repository README](../README.md).

```bash
cp .env.example .env.local
npm install
npm run dev
```

`NEXT_PUBLIC_API_BASE` is the FastAPI origin (default `http://127.0.0.1:8000`). It is not an LLM key.
