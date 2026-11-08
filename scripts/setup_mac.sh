#!/usr/bin/env bash
# Sets up this Mac as the photo booth server (ADR-034). Safe to run again: finished parts are skipped,
# existing settings are kept, and a running server is left running.
#
#   cd ~/photobox-server && scripts/setup_mac.sh [--check]
#
#   --check  only report what is there and what would be done; changes nothing
#
# Three things stay with a person, and the script asks for them: the consent notice, the dashboard
# password, and confirming that the counterpart images may be used. Without a terminal to ask in,
# they are listed at the end instead.
set -euo pipefail

MFLUX_VERSION=0.24.0
MFLUX_PYTHON=3.12
MODEL_DIRECTORY="$HOME/.cache/huggingface/hub/models--black-forest-labs--FLUX.2-klein-4B"
MINIMUM_FREE_KB=5000000
MINIMUM_FREE_KB_WITH_MODEL=40000000
export PATH="$HOME/.local/bin:$PATH"

fail() {
    echo "Stopped: $1" >&2
    exit 1
}

CHECK=false
while [ "$#" -gt 0 ]; do
    case "$1" in
        --check) CHECK=true; shift ;;
        *) fail "unknown option $1" ;;
    esac
done

cd "$(dirname "$0")/.."
REPOSITORY="$(pwd -P)"
PYTHON="$REPOSITORY/.venv/bin/python"
MFLUX_EDIT="$HOME/.local/bin/mflux-generate-flux2-edit"
MFLUX_GENERATE="$HOME/.local/bin/mflux-generate-flux2"
USER_DOMAIN="gui/$(id -u)"
HOME_REAL="$(cd "$HOME" && pwd -P)"
TODO=""

add_todo() {
    TODO="$TODO  - $1"$'\n'
}

model_ready() {
    [ -d "$MODEL_DIRECTORY/snapshots" ] && [ -z "$(find "$MODEL_DIRECTORY/blobs" -name '*.incomplete' -print 2>/dev/null | head -n 1)" ]
}

interactive() {
    [ -t 0 ]
}

setting() {
    [ -f .env ] || return 0
    sed -n "s/^$1=//p" .env | tail -n 1 | tr -d "\"'"
}

read_status() {
    read -r NOTICE SEEDS PASSWORD <<<"$("$PYTHON" -c '
from photobox_server.config import load_env_file
load_env_file()
from photobox_server.consent_notice import NoticeError, load_notice
from photobox_server.core import db_connect
try:
    load_notice()
    notice = "yes"
except NoticeError:
    notice = "no"
with db_connect() as db:
    seeds = db.execute("SELECT COUNT(*) FROM seeds WHERE removed_at IS NULL").fetchone()[0]
    password = db.execute("SELECT COUNT(*) FROM dashboard_password").fetchone()[0]
print(notice, seeds, "yes" if password else "no")
')"
}

# launchd cannot read these folders, so the services would fail with a confusing error.
case "$REPOSITORY/" in
    "$HOME_REAL/Documents/"*|"$HOME_REAL/Desktop/"*|"$HOME_REAL/Downloads/"*)
        fail "macOS does not let background services read this folder. Put the code in ~/photobox-server (found $REPOSITORY)" ;;
esac
[ "$(uname -m)" = arm64 ] || fail "this needs a Mac with Apple Silicon (found $(uname -m))"
# Only needed to create the environment. One that exists brings its own Python.
if [ ! -x "$PYTHON" ]; then
    command -v python3 >/dev/null || fail "Python is missing. Install it from python.org/downloads, open a new Terminal and run this again"
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || fail "Python 3.11 or newer is needed. Install it from python.org/downloads, open a new Terminal and run this again"
fi
needed="$MINIMUM_FREE_KB"
model_ready || needed="$MINIMUM_FREE_KB_WITH_MODEL"
free_kb="$(df -Pk "$HOME" | awk 'NR==2 {print $4}')"
[ "$free_kb" -ge "$needed" ] || fail "$((needed / 1000000)) GB of free space is needed (found $((free_kb / 1000000)) GB)"

INTERFACE="$(setting PHOTOBOX_INTERFACE)"
if [ -z "$INTERFACE" ] && [ -f .env ]; then
    INTERFACE=en0
fi
if [ -z "$INTERFACE" ]; then
    INTERFACE="$(route -n get default 2>/dev/null | awk '/interface:/ {print $2; exit}' || true)"
fi
[ -n "$INTERFACE" ] || fail "this Mac has no network connection"
ADDRESS="$(ipconfig getifaddr "$INTERFACE" 2>/dev/null || true)"
[ -n "$ADDRESS" ] || fail "the network interface $INTERFACE has no address. Connect it, or set PHOTOBOX_INTERFACE in .env"
PORT="$(setting PHOTOBOX_PORT)"
PORT="${PORT:-8880}"

if [ "$CHECK" = true ]; then
    echo "Network: $INTERFACE, $ADDRESS"
    [ -x "$PYTHON" ] && echo "Python environment: there" || echo "Python environment: would be created"
    [ -x "$MFLUX_EDIT" ] && echo "Image tool: there" || echo "Image tool: would be installed"
    model_ready && echo "Image model: there" || echo "Image model: would be downloaded, about 15 GB"
    [ -f .env ] && echo "Settings: there, kept as they are" || echo "Settings: would be written"
    if [ -x "$PYTHON" ] && [ -f "$(setting PHOTOBOX_STORAGE_DIR)/photobox.sqlite3" ]; then
        read_status
        echo "Consent notice ready: $NOTICE"
        echo "Counterparts: $SEEDS"
        echo "Dashboard password set: $PASSWORD"
    fi
    for name in server dashboard; do
        launchctl print "$USER_DOMAIN/com.photobox.$name" >/dev/null 2>&1 && echo "Service $name: running" || echo "Service $name: would be installed"
    done
    echo "Check finished. Nothing was changed."
    exit 0
fi

echo "== 1/6 Photo booth program"
[ -x "$PYTHON" ] || python3 -m venv .venv
"$PYTHON" -m pip install --quiet -e '.[server-ai]'

echo "== 2/6 Image tool and model"
if ! command -v uv >/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    command -v uv >/dev/null || fail "the helper uv could not be installed"
fi
[ -x "$MFLUX_EDIT" ] || uv tool install --python "$MFLUX_PYTHON" "mflux==$MFLUX_VERSION"
if model_ready; then
    echo "Already there: the image model"
else
    echo "Downloading the image model, about 15 GB. This can take a while ..."
    "$MFLUX_GENERATE" --model flux2-klein-4b -q 8 --prompt "a red apple on a wooden table" --steps 4 --width 512 --height 512 --output "$HOME/.photobox-model-test.png"
    rm -f "$HOME/.photobox-model-test.png"
    model_ready || fail "the image model did not finish downloading. Run this again to continue"
fi

echo "== 3/6 Settings"
if [ -f .env ]; then
    echo "Already there: .env, kept as it is"
else
    (
        umask 077
        sed -e 's/^PHOTOBOX_PROVIDER=mock/PHOTOBOX_PROVIDER=mflux-flux2/' \
            -e 's/^PHOTOBOX_DEVELOPER_MODE=true/PHOTOBOX_DEVELOPER_MODE=false/' \
            -e "s|^# PHOTOBOX_MFLUX_COMMAND=.*|PHOTOBOX_MFLUX_COMMAND=$MFLUX_EDIT|" .env.example > .env
        printf '\nPHOTOBOX_INTERFACE=%s\n' "$INTERFACE" >> .env
    )
fi
mkdir -p storage
chmod 700 storage
tmutil addexclusion storage || add_todo "Keep visitors' photos out of backups: tmutil addexclusion $REPOSITORY/storage"

echo "== 4/6 Consent notice"
read_status
if [ "$NOTICE" = yes ]; then
    echo "Already there: a usable consent notice"
else
    [ -f consent-notice.txt ] || cp consent-notice.example.txt consent-notice.txt
    if interactive; then
        open -e consent-notice.txt
        echo "The notice is open in TextEdit. Replace both TODO-OPERATOR entries, the minimum age and who is responsible,"
        echo "and save with Cmd+S as plain text. Have the text checked before the booth is used with the public."
        read -r -p "Press ENTER when it is saved: " _ || true
        read_status
    fi
    [ "$NOTICE" = yes ] || add_todo "Consent notice: replace both TODO-OPERATOR entries in $REPOSITORY/consent-notice.txt. Until then the booth shows \"not ready\""
fi

echo "== 5/6 Counterparts and dashboard password"
counterpart() {  # number, prompt
    "$MFLUX_GENERATE" --model flux2-klein-4b -q 8 --prompt "$2" --steps 4 --width 768 --height 1024 --output "$HOME/counterpart-$1.png"
}
if [ "$SEEDS" -gt 0 ]; then
    echo "Already there: $SEEDS counterparts"
elif interactive; then
    echo "Creating two invented people, about a minute each ..."
    counterpart 1 "Studio portrait photograph of a fictional woman in her sixties with short grey hair and round glasses, dark green sweater, looking at the camera, plain grey background"
    counterpart 2 "Studio portrait photograph of a fictional man in his thirties with a short beard, mustard yellow shirt, looking at the camera, plain light background"
    open "$HOME/counterpart-1.png" "$HOME/counterpart-2.png"
    answer=""
    read -r -p "Look at both pictures. Type yes to confirm they are invented people you may use: " answer || true
    if [ "$answer" = yes ]; then
        for number in 1 2; do
            "$PYTHON" -m photobox_server.admin seed add "$HOME/counterpart-$number.png" --rights-reference FICTIONAL-AI --attest-rights
            rm -f "$HOME/counterpart-$number.png"
        done
    else
        add_todo "Counterparts: none were added. The two pictures are in your home folder; see INSTALL.md to add images you may use"
    fi
else
    add_todo "Counterparts: every strip needs at least one. Run this script again in a Terminal to create them"
fi
if [ "$PASSWORD" = yes ]; then
    echo "Already there: the dashboard password"
elif interactive; then
    "$PYTHON" -m photobox_server.admin dashboard set-password || add_todo "Dashboard password: not set. Run this script again"
else
    add_todo "Dashboard password: run this script again in a Terminal"
fi

echo "== 6/6 Starting by itself"
install_service() {
    local label="com.photobox.$1" target rendered
    target="$HOME/Library/LaunchAgents/$label.plist"
    rendered="$(sed -e "s|REPOSITORY|$REPOSITORY|g" -e "s|HOME|$HOME|g" "deploy/mac/$label.plist")"
    if launchctl print "$USER_DOMAIN/$label" >/dev/null 2>&1; then
        if [ -f "$target" ] && [ "$rendered" = "$(cat "$target")" ]; then
            echo "Already running: $1"
            return
        fi
        launchctl bootout "$USER_DOMAIN/$label" || true
    fi
    mkdir -p "$HOME/Library/LaunchAgents"
    printf '%s\n' "$rendered" > "$target"
    launchctl bootstrap "$USER_DOMAIN" "$target"
}
install_service server
install_service dashboard

healthy=false
for _ in $(seq 1 30); do
    if curl -fsS -m 3 "http://$ADDRESS:$PORT/api/v1/health" >/dev/null 2>&1; then
        healthy=true
        break
    fi
    sleep 2
done
[ "$healthy" = true ] || fail "the server did not answer on $ADDRESS:$PORT. Its log is ~/Library/Logs/photobox-server.log"

echo
echo "Finished. The server runs on $ADDRESS:$PORT, and the dashboard is at http://127.0.0.1:8881 on this Mac."
echo "If macOS asks whether Python may accept incoming connections, click Allow, or the Pi cannot reach this Mac."
echo "Next, set up the Pi: scripts/setup_pi.sh admin@fotobox.local --server $ADDRESS --reboot"
if [ -n "$TODO" ]; then
    echo
    echo "Still to do:"
    printf '%s' "$TODO"
fi
