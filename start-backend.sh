#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Prefer repo-root .venv (survives package rename). Use absolute paths so a
# relocated venv still works even if activate/shebangs still say "storyforge".
if [[ -x "$ROOT/.venv/bin/python3" ]]; then
  PY="$ROOT/.venv/bin/python3"
elif [[ -x "$ROOT/lenswrite-backend/.venv/bin/python3" ]]; then
  PY="$ROOT/lenswrite-backend/.venv/bin/python3"
else
  echo "No venv found. Create one:"
  echo "  python3 -m venv .venv && source .venv/bin/activate && pip install fastapi uvicorn sse-starlette pydantic python-dotenv httpx langchain langchain-openai langgraph"
  exit 1
fi

# Smoke-check: console scripts may still have a broken storyforge shebang after rename.
if ! "$PY" -c "import uvicorn" 2>/dev/null; then
  echo "uvicorn not installed in $PY"
  echo "  $PY -m pip install uvicorn fastapi ..."
  exit 1
fi

export PYTHONPATH="$ROOT/lenswrite-backend${PYTHONPATH:+:$PYTHONPATH}"
# Local default: no cloud PORT; bind loopback for dev
export USE_HTTP_PROXY="${USE_HTTP_PROXY:-1}"
# python -m uvicorn avoids broken #! paths in .venv/bin/uvicorn after directory rename
exec "$PY" -m uvicorn app.main:app --reload --host 127.0.0.1 --port "${PORT:-8000}"
