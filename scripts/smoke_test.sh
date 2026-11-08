#!/usr/bin/env bash
set -euo pipefail
python -m photobox_client.cli --server "${PHOTOBOX_SERVER_URL:-http://127.0.0.1:8880}" health
pytest -q
