#!/usr/bin/env bash
# M1 acceptance test. Run on the Pi with the virtualenv active.
#
#   PHOTOBOX_DEVICE_TOKEN=... scripts/m1_acceptance.sh http://MAC_LAN_IP:8880 CAMERA --attest-documented-consent [--rotate 90]
#
# Point the camera at a synthetic scene or an authorized subject. The script uploads 20 real
# captures, so it needs the same operator attestation as `capture`. Orientation is checked by eye.
set -uo pipefail

if [ "$#" -lt 3 ] || [ "$3" != "--attest-documented-consent" ]; then
    echo "Usage: PHOTOBOX_DEVICE_TOKEN=... $0 SERVER_URL CAMERA --attest-documented-consent [--rotate DEGREES]" >&2
    exit 2
fi
SERVER=$1; CAMERA=$2; ATTEST=$3; shift 3
: "${PHOTOBOX_DEVICE_TOKEN:?Set PHOTOBOX_DEVICE_TOKEN first}"
CHECK_DIR=$(mktemp -d)
FAILURES=0
pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; FAILURES=$((FAILURES + 1)); }
cli() { python3 -m photobox_client.cli --server "$SERVER" "$@"; }

cli health > /dev/null && pass "server reachable" || fail "server reachable"

STATUS=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SERVER/api/v1/sessions")
[ "$STATUS" = "401" ] && pass "request without token rejected" || fail "request without token rejected (got $STATUS)"

if python3 -m photobox_client.cli camera-probe --camera /dev/photobox-invalid-camera > /dev/null 2>&1; then
    fail "invalid camera gives an error"
else
    pass "invalid camera gives an error"
fi

python3 -m photobox_client.cli camera-probe --camera "$CAMERA" || fail "camera probe"
cli camera-test --camera "$CAMERA" "$@" --save-dir "$CHECK_DIR" > /dev/null \
    && pass "orientation sample saved in $CHECK_DIR" || fail "orientation sample"

UPLOADS=0
for number in $(seq 1 20); do
    if cli capture --camera "$CAMERA" "$@" "$ATTEST" --no-process > /dev/null; then
        UPLOADS=$((UPLOADS + 1))
    else
        echo "Capture $number failed"
    fi
done
[ "$UPLOADS" = "20" ] && pass "20 consecutive authenticated uploads" || fail "authenticated uploads: $UPLOADS/20"

echo
echo "Still to do by hand: open $CHECK_DIR/camera-test-001.jpg, check the orientation, then delete it:"
echo "  rm -r $CHECK_DIR"
exit "$FAILURES"
