#!/usr/bin/env bash
# Starts the server on the Mac's current LAN address (ADR-010), never 0.0.0.0.
# Used by deploy/mac/com.photobox.server.plist; reads settings from .env in the repository.
set -euo pipefail
cd "$(dirname "$0")/.."
INTERFACE="${PHOTOBOX_INTERFACE:-en0}"
HOST="$(ipconfig getifaddr "$INTERFACE" || true)"
if [ -z "$HOST" ]; then
    echo "No LAN address on $INTERFACE yet; retrying later." >&2
    exit 1
fi
exec .venv/bin/python -m uvicorn photobox_server.app:app --host "$HOST" --port "${PHOTOBOX_PORT:-8000}" --log-level info
