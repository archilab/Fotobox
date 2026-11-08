#!/bin/sh
# Installs what lets the booth run unattended (ADR-032). Run once with sudo.
set -e
cd /home/admin/photobox/deploy/pi
cp photobox-kiosk.service photobox-check.service photobox-check.timer photobox-nightly.service photobox-nightly.timer /etc/systemd/system/
mkdir -p /etc/systemd/system.conf.d
cp watchdog.conf /etc/systemd/system.conf.d/photobox-watchdog.conf
systemctl daemon-reload
systemctl daemon-reexec
systemctl enable photobox-kiosk
systemctl enable --now photobox-check.timer photobox-nightly.timer
echo "Done: starts at boot, restarts when silent, restarts nightly at 04:30, hardware watchdog on."
