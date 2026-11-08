#!/usr/bin/env bash
# Starts the server on the Mac's current LAN address (ADR-010), never 0.0.0.0.
# Used by deploy/mac/com.photobox.server.plist; reads settings from .env in the repository.
set -euo pipefail
cd "$(dirname "$0")/.."
# The shell does not load .env, so the two settings used here are read from it directly.
from_env_file() {
    [ -f .env ] || return 0
    sed -n "s/^$1=//p" .env | tail -n 1 | tr -d "\"'"
}
INTERFACE="${PHOTOBOX_INTERFACE:-$(from_env_file PHOTOBOX_INTERFACE)}"
INTERFACE="${INTERFACE:-en0}"
PORT="${PHOTOBOX_PORT:-$(from_env_file PHOTOBOX_PORT)}"
PORT="${PORT:-8880}"
HOST="$(ipconfig getifaddr "$INTERFACE" || true)"
if [ -z "$HOST" ]; then
    echo "No LAN address on $INTERFACE yet; retrying later." >&2
    exit 1
fi
exec .venv/bin/python -m uvicorn photobox_server.app:app --host "$HOST" --port "$PORT" --log-level info --timeout-graceful-shutdown 5
