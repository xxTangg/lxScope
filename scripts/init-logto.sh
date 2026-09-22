#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Compose reads .env for itself, but a standalone shell does not export those
# values. Load the local deployment file so the documented command works after
# copying .env.example to .env.
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

required_vars=(
  LOGTO_ENDPOINT
  LOGTO_M2M_APP_ID
  LOGTO_M2M_APP_SECRET
  LOGTO_MANAGEMENT_API_RESOURCE
)
missing_vars=()
for variable in "${required_vars[@]}"; do
  if [[ -z "${!variable:-}" ]]; then
    missing_vars+=("$variable")
  fi
done

if (( ${#missing_vars[@]} > 0 )); then
  echo "[ERROR] Missing required environment variable(s): ${missing_vars[*]}" >&2
  echo "        Copy .env.example to .env and configure the Logto M2M app first." >&2
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="${PYTHON_BIN:-python3}"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="${PYTHON_BIN:-python}"
else
  echo "[ERROR] Python 3 is required to run the Logto bootstrap." >&2
  exit 1
fi

exec "$PYTHON_BIN" "$ROOT_DIR/deploy/logto/bootstrap.py" "$@"
