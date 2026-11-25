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
exec chromium --lang=de --kiosk --incognito --ozone-platform=wayland --user-data-dir="$profile" \
    --no-first-run --no-default-browser-check --noerrdialogs --disable-infobars --disable-translate \
    --disable-extensions --disable-sync --disable-default-apps --disable-background-networking \
    --disable-component-update --disable-features=Translate,MediaRouter,OptimizationHints \
    --password-store=basic --disk-cache-size=1 --overscroll-history-navigation=0 --disable-pinch \
    "$address"
