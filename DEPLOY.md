# LensWrite — 拆开部署（生产）

前后端 **分开部署**：

| 服务 | 推荐平台 | 仓库路径 |
|------|----------|----------|
| **API** | [Railway](https://railway.app) 或 [Render](https://render.com) | `lenswrite-backend/` |
| **UI** | [Vercel](https://vercel.com) | `lenswrite-frontend/` |

浏览器 → `NEXT_PUBLIC_API_BASE` → FastAPI → xAI（`XAI_API_KEY`）。

---

## 0. 上线前检查

- [ ] GitHub 仓库已推送（当前：`PaulLi515/lenswrite`）
- [ ] 已有 **xAI API Key**
- [ ] 本地 `lenswrite-backend/.env` **不要**提交；云端用控制台填环境变量

---

## 1. 部署后端 API（Railway 推荐）

### Railway

1. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub** → 选 `lenswrite`  
   （私有仓：GitHub Apps → Railway → 授权 `lenswrite`）
2. **二选一（推荐 A）**  
   - **A.** Service → **Settings → Root Directory** = `lenswrite-backend`  
   - **B.** 不设 Root：仓库根目录的 `Dockerfile` / `railway.toml` 会只构建后端 API
3. **Settings → Build**  
   - Builder：**Dockerfile**（不要用 Nixpacks 扫 monorepo 根，容易误建前端）  
   - Dockerfile path：Root 用 A 时是 `Dockerfile`；用 B 时也是根目录 `Dockerfile`
4. **Variables**（必须）：

| Key | Value |
|-----|--------|
| `XAI_API_KEY` | 你的 xAI key |
| `USE_HTTP_PROXY` | `0` |
| `CORS_ORIGINS` | 先填占位，前端上线后再改成 Vercel URL，如 `https://xxx.vercel.app` |
| `PYTHONUNBUFFERED` | `1` |

5. **Settings → Deploy**  
   - Start command：  
     `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
6. **Settings → Networking → Generate Domain**  
   记下 API 公网地址，例如：  
   `https://lenswrite-api-production.up.railway.app`
7. 浏览器打开：`https://<你的-api-域名>/health`  
   应返回 `"status":"ok"` 且 `"has_xai_key": true`

#### 若出现 `Failed to build an image`

1. 打开失败部署 → **View logs**，把 **Build** 段完整错误贴出来  
2. 确认 **Root Directory** 是 `lenswrite-backend` **或** 使用根目录 Dockerfile（本仓库已提供）  
3. Builder 改成 **Dockerfile**，Redeploy  
4. 不要用空 Root + Nixpacks：Nixpacks 会看到 `lenswrite-frontend/package.json`，容易建错项目

#### 若出现 `Attempt #N failed with service unavailable`

这是 **健康检查失败**（进程没在监听 / 启动崩溃），不是镜像构建问题。

1. **Variables** 必须有 `XAI_API_KEY`（没有也能启动并过 `/health`，但生成故事会失败；建议立刻配上）  
2. 必须有 `USE_HTTP_PROXY=0`（避免误用本地代理）  
3. 打开 **Deploy logs**（不是 Build）：  
   - 应看到 `Uvicorn running on http://0.0.0.0:...`  
   - 若有 `XAI_API_KEY is not set` / `ModuleNotFoundError` / traceback → 把日志贴出来  
4. **Networking → Generate Domain** 后再访问 `https://域名/health`  
5. Settings → Healthcheck Path 应为 `/health`；超时可先放到 300s

### Render（备选）

1. **New → Web Service** → 连 GitHub `lenswrite`
2. **Root Directory** = `lenswrite-backend`
3. **Build**: `pip install -r requirements.txt`
4. **Start**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. 同样设置 `XAI_API_KEY`、`USE_HTTP_PROXY=0`、`CORS_ORIGINS`

### Docker（可选）

```bash
cd lenswrite-backend
docker build -t lenswrite-api .
docker run -p 8000:8000 -e XAI_API_KEY=... -e USE_HTTP_PROXY=0 -e CORS_ORIGINS=https://your-app.vercel.app lenswrite-api
```

---

## 2. 部署前端（Vercel）

1. [vercel.com](https://vercel.com) → **Add New Project** → 导入 `PaulLi515/lenswrite`
2. **Root Directory** = `lenswrite-frontend`（**必填**；Import 时点 **Edit**，或之后  
   Project → **Settings → General → Root Directory → Edit** → 填 `lenswrite-frontend` → Save）
3. Framework：**Next.js**（不要 Other / 不要选成 Python）
4. Build & Output（一般默认即可）：
   - Install: `npm install`
   - Build: `npm run build`
   - Output: **留空**（Next.js 不要手填 `.next`）
5. **Environment Variables**：

| Key | Value |
|-----|--------|
| `NEXT_PUBLIC_API_BASE` | 上一步的 API 公网 URL，**无末尾斜杠**  
| | 例：`https://lenswrite-api-production.up.railway.app` |

6. Deploy → 记下前端 URL，例如 `https://lenswrite.vercel.app`

### Vercel 打开是 404（最常见）

几乎都是 **Root Directory 仍是仓库根目录**，Vercel 找不到 Next 的 `app/`。

修复：

1. Vercel 项目 → **Settings → General → Root Directory**  
2. 设为 **`lenswrite-frontend`**（不要带前后 `/`）  
3. Save → **Deployments → 最新一次 → ⋯ → Redeploy**（勾选重建）

Deploy 日志里应看到类似：

- `Cloning ...` 后工作目录含 `lenswrite-frontend` 的 `package.json`
- `Detected Next.js`
- `✓ Compiled successfully` / `Build Completed`

若日志是 Python / 找不到 `package.json` / 成功但页面 404，就是 Root 仍不对。

其它可能：

| 现象 | 处理 |
|------|------|
| 页面 `404: NOT_FOUND` | 改 Root Directory 后 **Redeploy** |
| 页面能开但生成失败 | 查 `NEXT_PUBLIC_API_BASE` + Railway `CORS_ORIGINS` |
| 打开的是 `xxx.up.railway.app` 却当网页 | 那是 API；前端要用 `*.vercel.app` |

### 重要：回写 CORS（否则浏览器报 Disallowed CORS origin）

回到 Railway → **API Service → Variables**，设置：

```text
CORS_ORIGINS=https://你的前端.vercel.app
```

**格式要求（错一个就会被拦）：**

| 正确 | 错误 |
|------|------|
| `https://xxx.vercel.app` | `xxx.vercel.app`（缺 https） |
| 无末尾 `/` | `https://xxx.vercel.app/` |
| 与浏览器地址栏 **完全一致** | 用了 preview 域名却只写了 production |

多个域名用英文逗号：

```text
CORS_ORIGINS=https://lenswrite.vercel.app,https://lenswrite-git-main-xxx.vercel.app
```

可选：允许所有 Vercel 域名（含预览）：

```text
CORS_ORIGIN_REGEX=1
```

改完后 **Redeploy API**。Deploy logs 应出现：

```text
[LensWrite] CORS origins: ['https://xxx.vercel.app', ...]
```

---

## 3. 验收清单

1. `GET https://<api>/health` → `ok` + `has_xai_key: true`
2. 打开 Vercel 前端 → 填 premise → Generate  
3. 浏览器 Network：请求应打到 `NEXT_PUBLIC_API_BASE`，状态 200  
4. 等 1–5 分钟拿成稿（LLM 较慢属正常）

---

## 4. 环境变量总表

### API（Railway / Render）

| 变量 | 必需 | 说明 |
|------|------|------|
| `XAI_API_KEY` | ✅ | xAI key |
| `CORS_ORIGINS` | ✅ 生产 | 前端 origin，逗号分隔 |
| `USE_HTTP_PROXY` | 生产设 `0` | 云端不要走本地 VPN 代理 |
| `XAI_MODEL` | 可选 | 默认 `grok-3` |
| `PORT` | 平台注入 | 勿手写死 |

### UI（Vercel）

| 变量 | 必需 | 说明 |
|------|------|------|
| `NEXT_PUBLIC_API_BASE` | ✅ | API 公网 base，无 `/` 结尾 |

### 本地开发（与生产对照）

```bash
# lenswrite-backend/.env
XAI_API_KEY=...
USE_HTTP_PROXY=1          # 仅本机需要 VPN 时
VPN_HTTP_PROXY=http://127.0.0.1:4780
CORS_ORIGINS=http://localhost:3000

# lenswrite-frontend/.env.local
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000
```

---

## 5. 架构示意

```
用户浏览器
    │
    ▼
Vercel (Next.js)  ──NEXT_PUBLIC_API_BASE──►  Railway/Render (FastAPI)
                                                    │
                                                    ▼
                                              xAI API (grok)
```

- 前端是静态/SSR，**不持有** xAI key  
- 密钥只在 API 进程  
- 长请求：生成可能 1–5+ 分钟；Railway/Render 注意 **HTTP timeout**（免费层可能较短）。若遇超时，可改用轮询（现 UI 已轮询 status）并确保平台不杀后台 task。

---

## 6. 常见问题

| 现象 | 处理 |
|------|------|
| CORS error | `CORS_ORIGINS` 必须与浏览器地址栏 origin **完全一致**（含 https） |
| `has_xai_key: false` | API 环境变量未设 / 未 redeploy |
| 前端仍打 localhost | Vercel 未设 `NEXT_PUBLIC_API_BASE` 或设完未重新 Build |
| 502 / timeout | 生成太长；确认 status 轮询仍通；升级 API 实例 |
| 本机 VPN 代理残留 | 云端 `USE_HTTP_PROXY=0`，不要拷贝本地 `HTTPS_PROXY=127.0.0.1:4780` |

---

## 7. 一键顺序（最快路径）

1. Railway 部署 `lenswrite-backend` + `XAI_API_KEY` + `USE_HTTP_PROXY=0`  
2. 拿 API 域名 → 测 `/health`  
3. Vercel 部署 `lenswrite-frontend` + `NEXT_PUBLIC_API_BASE=<API>`  
4. 拿前端域名 → 写回 `CORS_ORIGINS` → Redeploy API  
5. 打开前端生成一篇短故事验收  

完成。
