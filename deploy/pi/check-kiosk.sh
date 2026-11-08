#!/bin/sh
# Restarts a kiosk that has stopped showing signs of life (ADR-032). Run by photobox-check.timer as root.
# With --nightly, restarts it once to clear whatever a long run has built up, but only at the start screen.
heartbeat=/dev/shm/photobox-heartbeat
systemctl is-active -q photobox-kiosk || exit 0
[ -f "$heartbeat" ] || exit 0
age=$(( $(date +%s) - $(stat -c %Y "$heartbeat") ))

if [ "$1" = "--nightly" ]; then
    if [ "$(cat "$heartbeat")" = "idle" ] && [ "$age" -lt 60 ]; then
        logger -t photobox "nightly kiosk restart"
        exec systemctl restart photobox-kiosk
    fi
    exit 0
fi

if [ "$age" -gt 180 ]; then
    logger -t photobox "kiosk silent for $age s, restarting it"
    # A kiosk this stuck may ignore a polite stop, and the login session keeps it out of systemd's reach.
    pkill -KILL -x cage
    pkill -KILL -f 'photobox_client\.kiosk'
    exec systemctl restart photobox-kiosk
fi
