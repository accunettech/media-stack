#!/usr/bin/env bash
set -euo pipefail
STACK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$STACK_ROOT/.venv/bin/python" ]]; then
  echo "Run ./setup.sh first to install the configuration loader." >&2
  exit 1
fi
exec "$STACK_ROOT/.venv/bin/python" "$STACK_ROOT/scripts/stack_env.py" compose "$@"
