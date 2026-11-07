from __future__ import annotations
import argparse
import json
import os
import stat
import sys
import time
import uuid
from pathlib import Path
import httpx
from .camera import ROTATIONS, CameraError, CaptureSettings, capture_jpeg, list_stable_paths, probe_camera

TOKEN_ENV = "PHOTOBOX_DEVICE_TOKEN"
COMMANDS_NEEDING_TOKEN = {"capture", "status", "result", "cancel"}
POLL_SECONDS = 2.0
NETWORK_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.0


def read_token(token_file: Path | None) -> str | None:
    """Tokens come from a file or the environment, never argv, which other users can see."""
    if token_file is None:
        return os.environ.get(TOKEN_ENV, "").strip() or None
    if os.name == "posix" and stat.S_IMODE(token_file.stat().st_mode) & 0o077:
        print(f"Warning: {token_file} is readable by other users; run: chmod 600 {token_file}", file=sys.stderr)
    return token_file.read_text(encoding="utf-8").strip() or None


def add_camera_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--camera", default="0", help="Index, /dev/ path or stable name from camera-list (default: 0)")
    parser.add_argument("--width", type=int, help="Requested frame width")
    parser.add_argument("--height", type=int, help="Requested frame height")
    parser.add_argument("--rotate", type=int, default=0, choices=ROTATIONS, help="Clockwise rotation in degrees")
    parser.add_argument("--jpeg-quality", type=int, default=90, help="JPEG quality 1-100 (default: 90)")
    parser.add_argument("--attempts", type=int, default=3, help="Capture attempts before giving up (default: 3)")


def post_with_retry(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    """POST, repeating only after a connection problem, where the server's answer was lost.

    Each request here is safe to repeat: the server recognizes it by its Idempotency-Key or state.
    """
    for attempt in range(1, NETWORK_ATTEMPTS + 1):
        try:
            return client.post(url, **kwargs)
        except httpx.TransportError as exc:
            if attempt == NETWORK_ATTEMPTS:
                raise
            print(f"Connection problem ({type(exc).__name__}), retrying {attempt}/{NETWORK_ATTEMPTS - 1}", file=sys.stderr)
            time.sleep(RETRY_DELAY_SECONDS * attempt)
    raise AssertionError("unreachable")


def wait_for_result(client: httpx.Client, session_id: str, wait_seconds: int) -> dict:
    """Follow a queued session until it finishes, showing progress when it changes."""
    deadline = time.monotonic() + wait_seconds
    shown = None
    while True:
        response = client.get(f"/api/v1/sessions/{session_id}")
        response.raise_for_status()
        state = response.json()
        job = state.get("job") or {}
        progress = (state["status"], job.get("frames_done"), job.get("frames_total"), job.get("attempts"))
        if progress != shown:
            shown = progress
            print(f"{state['status']}: frame {job.get('frames_done', 0)}/{job.get('frames_total', '?')}, attempt {job.get('attempts', 0)}")
        if state["status"] == "complete":
            return state
        if state["status"] in {"failed", "withdrawn", "uploaded"}:
            print(f"Error: generation ended as {state['status']}: {state.get('error') or 'no details'}", file=sys.stderr)
            raise SystemExit(1)
        if time.monotonic() >= deadline:
            print(f"Error: still {state['status']} after {wait_seconds} s. It continues on the server; check with: status {session_id}", file=sys.stderr)
            raise SystemExit(1)
        time.sleep(POLL_SECONDS)


def idempotency_header() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


def capture_settings(args: argparse.Namespace) -> CaptureSettings:
    return CaptureSettings(width=args.width, height=args.height, rotation=args.rotate, jpeg_quality=args.jpeg_quality, attempts=args.attempts)


def camera_test(args: argparse.Namespace) -> None:
    """Capture repeatedly. Images are discarded unless --save-dir is given."""
    if args.count < 1:
        raise CameraError("--count must be at least 1")
    if args.save_dir:
        print("Note: saved test images may show people. Keep them out of Git and delete them after checking.")
        args.save_dir.mkdir(parents=True, exist_ok=True)
    settings = capture_settings(args)
    failures = 0
    for number in range(1, args.count + 1):
        started = time.monotonic()
        try:
            payload = capture_jpeg(args.camera, settings)
        except CameraError as exc:
            failures += 1
            print(f"{number}/{args.count} FAILED: {exc}", file=sys.stderr)
            continue
        print(f"{number}/{args.count} ok: {len(payload)} bytes in {time.monotonic() - started:.2f}s")
        if args.save_dir:
            (args.save_dir / f"camera-test-{number:03d}.jpg").write_bytes(payload)
    print(f"Captures succeeded: {args.count - failures}/{args.count}")
    if failures:
        raise SystemExit(1)


def camera_list() -> None:
    paths = list_stable_paths()
    if not paths:
        print("No stable camera paths found (/dev/v4l/by-id exists only on Linux with a connected camera). Try --camera 0.")
    for path in paths:
        print(f"{path} -> {os.path.realpath(path)}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Connected Photobox phase 1 CLI")
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--token-file", type=Path, help=f"File holding this device's token (default: ${TOKEN_ENV})")
    parser.add_argument("--timeout", type=int, default=120)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("health")
    sub.add_parser("camera-list", help="List stable camera paths")
    add_camera_probe = sub.add_parser("camera-probe", help="Report camera format, size, FPS and controls")
    add_camera_probe.add_argument("--camera", default="0", help="Index, /dev/ path or stable name from camera-list (default: 0)")
    test = sub.add_parser("camera-test", help="Capture repeatedly without uploading")
    add_camera_arguments(test)
    test.add_argument("--count", type=int, default=1, help="Number of captures (default: 1)")
    test.add_argument("--save-dir", type=Path, help="Keep the images here to check orientation")
    cap = sub.add_parser("capture")
    cap.add_argument("--file", type=Path)
    add_camera_arguments(cap)
    cap.add_argument("--attest-documented-consent", action="store_true", help="Required: the subject gave documented consent outside this tool, or the image is synthetic")
    cap.add_argument("--pool-consent", action="store_true", help="Only when separate, documented pool authorization exists")
    cap.add_argument("--consent-reference", help="Reference to the outside consent document, such as a form number. Never personal data.")
    cap.add_argument("--no-process", action="store_true")
    cap.add_argument("--wait-seconds", type=int, default=1800, help="How long to wait for the strip (default: 1800)")
    st = sub.add_parser("status"); st.add_argument("session_id")
    can = sub.add_parser("cancel", help="Stop generation; the portrait stays"); can.add_argument("session_id")
    res = sub.add_parser("result"); res.add_argument("session_id"); res.add_argument("--output", type=Path, default=Path("photostrip.png")); res.add_argument("--profile", default="color", choices=("color", "grayscale", "thermal"), help="Output profile (default: color)")
    args = parser.parse_args(argv)
    if args.cmd == "capture" and not args.attest_documented_consent:
        parser.error("capture requires --attest-documented-consent: use it only when the subject gave documented consent outside this tool, or the image is synthetic")
    try:
        if args.cmd == "camera-list":
            camera_list()
            return
        if args.cmd == "camera-probe":
            print(json.dumps(probe_camera(args.camera), indent=2))
            return
        if args.cmd == "camera-test":
            camera_test(args)
            return
        token = read_token(args.token_file)
        if args.cmd in COMMANDS_NEEDING_TOKEN and token is None:
            parser.error(f"{args.cmd} needs a device token: set ${TOKEN_ENV} or use --token-file")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        with httpx.Client(base_url=args.server, timeout=args.timeout, headers=headers) as client:
            if args.cmd == "health":
                response = client.get("/api/v1/health")
            elif args.cmd == "status":
                response = client.get(f"/api/v1/sessions/{args.session_id}")
            elif args.cmd == "cancel":
                response = post_with_retry(client, f"/api/v1/sessions/{args.session_id}/cancel")
            elif args.cmd == "result":
                response = client.get(f"/api/v1/sessions/{args.session_id}/result", params={"profile": args.profile})
                response.raise_for_status()
                args.output.write_bytes(response.content)
                print(f"Saved: {args.output}")
                return
            else:
                print("DEVELOPMENT TEST ONLY: operator attested documented consent for image processing; --pool-consent additionally attests separate pool-use permission.")
                raw = args.file.read_bytes() if args.file else capture_jpeg(args.camera, capture_settings(args))
                response = post_with_retry(client, "/api/v1/sessions", headers=idempotency_header()); response.raise_for_status()
                sid = response.json()["id"]
                print(f"Session: {sid}")
                consent = {"processing": True, "pool": args.pool_consent, "documented_externally": args.attest_documented_consent}
                if args.consent_reference:
                    consent["evidence_ref"] = args.consent_reference
                c = post_with_retry(client, f"/api/v1/sessions/{sid}/consent", json=consent); c.raise_for_status()
                code = c.json().get("withdrawal_code")
                if code:
                    print(f"Withdrawal code, shown only now. Give it to the participant: {code}")
                else:
                    print(f"No withdrawal code was received. Consent can still be withdrawn on the server with session {sid}.", file=sys.stderr)
                up = post_with_retry(client, f"/api/v1/sessions/{sid}/portrait", files={"image": ("capture.jpg", raw, "image/jpeg")}, headers=idempotency_header()); up.raise_for_status()
                if args.no_process:
                    print("Uploaded; processing skipped.")
                    return
                response = post_with_retry(client, f"/api/v1/sessions/{sid}/process")
                response.raise_for_status()
                print(json.dumps(wait_for_result(client, sid, args.wait_seconds)))
                print(f"Get the strip with: result {sid}")
                return
            response.raise_for_status()
            print(response.json())
    except CameraError as exc:
        print(f"Camera error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except httpx.HTTPStatusError as exc:
        print(f"Error: server returned {exc.response.status_code}: {server_detail(exc.response)}", file=sys.stderr)
        raise SystemExit(1)
    except (httpx.HTTPError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)


def server_detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", "no details"))
    except (ValueError, AttributeError):
        return "no details"


if __name__ == "__main__":
    main()
