#!/bin/sh
# Starts the booth with the browser screen, and falls back to the text kiosk if the browser cannot run (ADR-028).
python=/home/admin/photobox/.venv/bin/python
browser=${PHOTOBOX_BROWSER_COMMAND:-/home/admin/photobox/deploy/pi/start-browser.sh}

"$python" -m photobox_client.kiosk "$@" --ui web --browser "$browser"
code=$?
if [ "$code" -eq 75 ]; then
    echo "The browser screen failed; starting the text kiosk." >&2
    exec "$python" -m photobox_client.kiosk "$@" --ui text
fi
exit "$code"
