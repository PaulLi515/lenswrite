# Security — before making this repo public

## Secrets stay out of git

| Safe to commit | Never commit |
|----------------|--------------|
| `.env.example` (empty placeholders) | `.env`, `.env.local`, `.env.production` |
| Docs that say `XAI_API_KEY=...` | Real `xai-…` / any API token |
| Railway / Vercel setup steps | Cloud dashboard secrets |

Ignored by `.gitignore`:

- `.env`, `.env.*` (except `.env.example`)
- `lenswrite-backend/.env`
- `lenswrite-frontend/.env.local`

## Where keys actually live

| Secret | Where to set it |
|--------|-----------------|
| `XAI_API_KEY` | Local: `lenswrite-backend/.env` · Cloud: **Railway Variables** only |
| `NEXT_PUBLIC_API_BASE` | Local: `lenswrite-frontend/.env.local` · Cloud: **Vercel env** (public URL only, not the xAI key) |
| `CORS_ORIGINS` | Railway Variables |

`NEXT_PUBLIC_*` is visible in the browser bundle — never put API keys there.

## Public-repo checklist

1. [ ] `git ls-files | grep env` shows only `*.env.example`
2. [ ] No real key in `git log -p -G 'xai-'` / GitHub secret scanning alerts
3. [ ] Rotate `XAI_API_KEY` at [console.x.ai](https://console.x.ai) if it was ever pasted into chat, screenshots, or commits
4. [ ] Update Railway Variable with the new key → Redeploy
5. [ ] Then: GitHub → Settings → Danger Zone → **Change visibility → Public**

## If a key leaked

1. Revoke / rotate immediately at the provider
2. Update Railway (and any other hosts)
3. If it was in git history, rewrite history or treat the key as burned (rotation is enough for API keys)
