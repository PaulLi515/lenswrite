#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python3" ]]; then
  PY="$ROOT/.venv/bin/python3"
elif [[ -x "$ROOT/lenswrite-backend/.venv/bin/python3" ]]; then
  PY="$ROOT/lenswrite-backend/.venv/bin/python3"
else
  echo "No venv found. Create one:"
  echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -r lenswrite-backend/requirements.txt"
  exit 1
fi

if ! "$PY" -c "import uvicorn" 2>/dev/null; then
  echo "uvicorn not installed in $PY"
  echo "  $PY -m pip install -r lenswrite-backend/requirements.txt"
  exit 1
fi

export PYTHONPATH="$ROOT/lenswrite-backend${PYTHONPATH:+:$PYTHONPATH}"
export USE_HTTP_PROXY="${USE_HTTP_PROXY:-0}"
exec "$PY" -m uvicorn app.main:app --reload --host 127.0.0.1 --port "${PORT:-8000}"
