#!/usr/bin/env bash
# Sets up a Raspberry Pi from this Mac: copies this folder to it and runs the installer there (ADR-033).
#
#   scripts/setup_pi.sh admin@fotobox.local --server 192.168.1.24 --reboot
#
# Other options are passed on to deploy/pi/install.sh, such as --check. Without an SSH key, the Pi asks
# for its password twice. Files git ignores, such as .env and visitors' photos, are never copied.
set -euo pipefail
if [ "$#" -lt 1 ] || [ "${1#-}" != "$1" ]; then
    echo "Usage: scripts/setup_pi.sh admin@PI_ADDRESS [--server ADDRESS] [--reboot] [--check]" >&2
    exit 2
fi
HOST="$1"
shift
cd "$(dirname "$0")/.."
echo "Copying the booth program to $HOST ..."
git ls-files -co --exclude-standard | COPYFILE_DISABLE=1 tar --no-xattrs -c -T - | ssh "$HOST" 'mkdir -p ~/photobox && tar -x -C ~/photobox'
arguments=""
if [ "$#" -gt 0 ]; then
    arguments="$(printf ' %q' "$@")"
fi
exec ssh -t "$HOST" "bash ~/photobox/deploy/pi/install.sh$arguments"
