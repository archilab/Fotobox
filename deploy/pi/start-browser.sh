#!/bin/sh
# Shows one address full screen in the display's own mode, with Cage as the only window manager (ADR-028).
# The booth's 7-inch panel runs at 1280 x 800, which the page is laid out for; it scales itself to any other screen.
if [ "$1" != "--inside" ]; then
    exec cage -d -- "$0" --inside "$1"
fi
address=$2

# A throwaway profile in memory: nothing about a visit stays on the disk.
profile="${XDG_RUNTIME_DIR:-/tmp}/photobox-browser"
rm -rf "$profile"
# German, like the page, so the browser never offers to translate it (see also deploy/pi/chromium-policy.json).
# Camera and microphone for the hidden video link (ADR-045) are granted without asking, because nobody could
# answer a prompt in the kiosk; the policy's VideoCaptureAllowedUrls alone left the page waiting on newbox.
# Chromium 156 otherwise asks a PipeWire camera portal, which Raspberry Pi OS Lite does not run, and waits forever
# (measured on newbox, 2026-11-26): without it Chromium uses the camera through V4L2 directly.
# The kiosk only ever shows the booth's own page on 127.0.0.1, under its strict security policy.
exec chromium --lang=de --kiosk --incognito --ozone-platform=wayland --user-data-dir="$profile" \
    --use-fake-ui-for-media-stream --autoplay-policy=no-user-gesture-required \
    --no-first-run --no-default-browser-check --noerrdialogs --disable-infobars --disable-translate \
    --disable-extensions --disable-sync --disable-default-apps --disable-background-networking \
    --disable-component-update --disable-features=Translate,MediaRouter,OptimizationHints,WebRtcPipeWireCamera,PipeWireCamera \
    --password-store=basic --disk-cache-size=1 --overscroll-history-navigation=0 --disable-pinch \
    "$address"
