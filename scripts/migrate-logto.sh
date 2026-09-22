#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
COMMAND="${1:-apply}"
if [[ "$COMMAND" == "apply" || "$COMMAND" == "export" ]]; then
  : "${LOGTO_ENDPOINT:?LOGTO_ENDPOINT is required}"
  : "${LOGTO_M2M_APP_ID:?LOGTO_M2M_APP_ID is required}"
  : "${LOGTO_M2M_APP_SECRET:?LOGTO_M2M_APP_SECRET is required}"
  : "${LOGTO_MANAGEMENT_API_RESOURCE:?LOGTO_MANAGEMENT_API_RESOURCE is required}"
fi

shift || true
exec "$PYTHON_BIN" "$ROOT_DIR/deploy/logto/migration.py" "$COMMAND" "$@"
