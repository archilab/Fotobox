#!/bin/sh
# Sets where the booth's browser plays and records sound, for its sounds (ADR-047) and the hidden video link (ADR-045).
#
#   sh deploy/pi/set-audio.sh PLAYBACK_CARD CAPTURE_CARD     for example: vc4hdmi0 BRIO
#   sh deploy/pi/set-audio.sh --test                         plays a short tone through the setting
#
# Raspberry Pi OS Lite has no sound server, so Chromium uses ALSA's "default" device. Its first card is often
# a webcam that can record but not play, so the setting pairs a playback card (an HDMI output through ALSA's
# hdmi device, which turns the sound into the HDMI format, or a USB speaker) with a recording card. It is
# written to ~/.asoundrc of the booth's user; the microphone for messages keeps its own setting.
set -eu
FILE="$HOME/.asoundrc"

if [ "${1:-}" = "--test" ]; then
    exec speaker-test -D default -c 2 -t sine -f 440 -l 1 -p 300000
fi
if [ "$#" -ne 2 ]; then
    echo "usage: $0 PLAYBACK_CARD CAPTURE_CARD   (card names as in /proc/asound/cards, such as vc4hdmi0 BRIO)" >&2
    exit 2
fi
for card in "$1" "$2"; do
    case "$card" in
        *[!A-Za-z0-9_]*|"") echo "not a card name: $card" >&2; exit 2 ;;
    esac
done
case "$1" in
    vc4hdmi*) playback="hdmi:CARD=$1,DEV=0" ;;
    *) playback="hw:CARD=$1,DEV=0" ;;
esac
umask 077
cat > "$FILE.new" <<EOF
# Written by photobox deploy/pi/set-audio.sh (ADR-045): play through $1, record from $2.
pcm.!default {
    type asym
    playback.pcm { type plug slave.pcm "$playback" }
    capture.pcm { type plug slave.pcm "hw:CARD=$2,DEV=0" }
}
ctl.!default { type hw card $1 }
EOF
mv "$FILE.new" "$FILE"
echo "Sound: playing through $1, recording from $2 ($FILE)."
