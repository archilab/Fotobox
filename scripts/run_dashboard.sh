#!/usr/bin/env bash
# Starts the operator dashboard on this Mac only (ADR-022). Never bind it to another address.
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m uvicorn photobox_server.dashboard:app --host 127.0.0.1 --port "${PHOTOBOX_DASHBOARD_PORT:-8881}" --log-level warning
