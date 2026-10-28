#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
# Always the renamed package — never a ghost storyforge-frontend/.next
cd "$ROOT/lenswrite-frontend"
# Drop stale Next artifacts that only exist under the old folder name
if [[ -d "$ROOT/storyforge-frontend" && ! -L "$ROOT/storyforge-frontend" ]]; then
  echo "Removing leftover directory storyforge-frontend (use lenswrite-frontend)"
  rm -rf "$ROOT/storyforge-frontend"
  ln -sfn lenswrite-frontend "$ROOT/storyforge-frontend"
fi
exec npm run dev
