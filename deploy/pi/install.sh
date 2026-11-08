#!/usr/bin/env bash
# Sets up a Raspberry Pi as a photo booth (ADR-033). Safe to run again: finished parts are skipped.
#
#   bash ~/photobox/deploy/pi/install.sh [--server ADDRESS] [--reboot] [--check]
#
#   --server ADDRESS  write the server's address, as the setup menu would
#   --reboot          restart the Pi at the end, which starts the booth
#   --check           only report what is there and what would be done; changes nothing
#
# Installs voice and person detection too, but switches neither on. The microphone and person detection
# are chosen in the setup menu; person detection needs the sign in person-detection-sign.example.txt (ADR-029).
set -euo pipefail

WHISPER_REPOSITORY=https://github.com/ggml-org/whisper.cpp.git
WHISPER_TAG=v1.7.4
WHISPER_COMMIT=8a9ad7844d6e2a10cddf4b92de4089d7ac2b14a9
WHISPER_MODEL_URL=https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin
WHISPER_MODEL_SHA256=60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe
PERSON_MODEL_URL=https://huggingface.co/opencv/object_detection_nanodet/resolve/main/object_detection_nanodet_2022nov.onnx
PERSON_MODEL_SHA256=4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186
MINIMUM_FREE_KB=2000000

fail() {
    echo "Stopped: $1" >&2
    exit 1
}

SERVER=""
REBOOT=false
CHECK=false
while [ "$#" -gt 0 ]; do
    case "$1" in
        --server)
            [ "$#" -ge 2 ] && [ -n "$2" ] || fail "--server needs an address, such as 192.168.1.24"
            SERVER="$2"
            shift 2
            ;;
        --reboot) REBOOT=true; shift ;;
        --check) CHECK=true; shift ;;
        *) fail "unknown option $1" ;;
    esac
done

REPOSITORY="$(cd "$(dirname "$0")/../.." && pwd -P)"
SPEECH="$HOME/photobox-speech"
PERSON="$HOME/photobox-person"
PYTHON="$REPOSITORY/.venv/bin/python"

# The system files name /home/admin/photobox, so these are requirements and not preferences.
[ "$(uname -m)" = aarch64 ] || fail "this needs 64-bit Raspberry Pi OS (found $(uname -m))"
[ "$(id -un)" = admin ] || fail "run this as the user admin (the booth's system files expect it)"
[ "$REPOSITORY" = "$(cd "$HOME" && pwd -P)/photobox" ] || fail "the code must be in ~/photobox (found $REPOSITORY)"
free_kb="$(df -Pk "$HOME" | awk 'NR==2 {print $4}')"
[ "$free_kb" -ge "$MINIMUM_FREE_KB" ] || fail "at least 2 GB of free space is needed (found $((free_kb / 1024)) MB)"
getent hosts huggingface.co >/dev/null || fail "no internet connection, or the name huggingface.co cannot be found"

has_hash() {
    [ -f "$1" ] && [ "$(sha256sum "$1" | cut -d ' ' -f 1)" = "$2" ]
}

if [ "$CHECK" = true ]; then
    sudo -n true 2>/dev/null && echo "Administrator rights: yes" || echo "Administrator rights: a password will be asked for"
    [ -x "$PYTHON" ] && echo "Python environment: there" || echo "Python environment: would be created"
    [ -x "$SPEECH/whisper-cli" ] && echo "Voice program: there" || echo "Voice program: would be built, about 10 minutes"
    has_hash "$SPEECH/ggml-base.bin" "$WHISPER_MODEL_SHA256" && echo "Voice model: correct" || echo "Voice model: would be downloaded, 148 MB"
    has_hash "$PERSON/nanodet.onnx" "$PERSON_MODEL_SHA256" && echo "Person model: correct" || echo "Person model: would be downloaded, 4 MB"
    echo "Check finished. Nothing was changed."
    exit 0
fi

build_directory=""
trap '[ -z "$build_directory" ] || rm -rf "$build_directory"' EXIT

fetch_verified() {  # URL, expected hash, destination
    if has_hash "$3" "$2"; then
        echo "Already there: $3"
        return
    fi
    echo "Downloading $(basename "$3") ..."
    curl -fL --retry 3 --connect-timeout 20 -o "$3.part" "$1" || { rm -f "$3.part"; fail "the download of $(basename "$3") failed"; }
    has_hash "$3.part" "$2" || { rm -f "$3.part"; fail "$(basename "$3") did not match its expected checksum, so it was discarded"; }
    mv "$3.part" "$3"
    chmod 644 "$3"
}

build_whisper() {
    if [ -x "$SPEECH/whisper-cli" ]; then
        echo "Already there: $SPEECH/whisper-cli"
        return
    fi
    echo "Building the speech program. This takes about 10 minutes ..."
    build_directory="$(mktemp -d "$HOME/.whisper-build.XXXXXX")"
    git clone --quiet --depth 1 --branch "$WHISPER_TAG" "$WHISPER_REPOSITORY" "$build_directory/source"
    [ "$(git -C "$build_directory/source" rev-parse HEAD)" = "$WHISPER_COMMIT" ] || fail "whisper.cpp $WHISPER_TAG is not the expected version"
    (
        cd "$build_directory/source"
        cmake -B build-static -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DWHISPER_BUILD_TESTS=OFF
        cmake --build build-static -j2 --target whisper-cli
    )
    install -m 755 "$build_directory/source/build-static/bin/whisper-cli" "$SPEECH/whisper-cli"
}

sudo -v
echo "== 1/5 System packages"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git curl python3-venv polkitd alsa-utils v4l-utils build-essential cmake
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends cage chromium wlr-randr

echo "== 2/5 Photo booth program"
cd "$REPOSITORY"
[ -x "$PYTHON" ] || python3 -m venv .venv
"$PYTHON" -m pip install --quiet -e '.[client-camera]'

echo "== 3/5 Voice"
install -d -m 700 "$SPEECH"
build_whisper
fetch_verified "$WHISPER_MODEL_URL" "$WHISPER_MODEL_SHA256" "$SPEECH/ggml-base.bin"

echo "== 4/5 Person detection (installed, not switched on)"
install -d -m 700 "$PERSON"
fetch_verified "$PERSON_MODEL_URL" "$PERSON_MODEL_SHA256" "$PERSON/nanodet.onnx"

echo "== 5/5 Starting by itself"
sudo install -m 644 deploy/pi/50-photobox-network.rules /etc/polkit-1/rules.d/50-photobox-network.rules
sudo install -d -m 755 /etc/chromium/policies/managed
sudo install -m 644 deploy/pi/chromium-policy.json /etc/chromium/policies/managed/photobox.json
sudo sh deploy/pi/install-autonomy.sh

if [ -n "$SERVER" ]; then
    # The same code as the setup menu, so the file keeps its format and every other setting.
    "$PYTHON" -c '
import sys
from pathlib import Path
from photobox_client.settings import Settings
from photobox_client.setup_menu import normalize_server
path = Path(sys.argv[1])
settings = Settings.load(path)
settings.server = normalize_server(sys.argv[2])
settings.save(path)
print("Server address:", settings.server)
' "$HOME/.photobox-kiosk.env" "$SERVER"
fi

echo
echo "Finished. At the booth, the setup menu opens by itself after the restart: choose a PIN, pair and choose the camera."
if [ "$REBOOT" = true ]; then
    echo "Restarting the Pi in a few seconds ..."
    sudo systemd-run --quiet --on-active=3 systemctl reboot
else
    echo "Restart the Pi to start the booth: sudo reboot"
fi
