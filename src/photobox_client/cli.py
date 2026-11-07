from __future__ import annotations
import argparse
import io
import sys
from pathlib import Path
import httpx

def take_photo(index: int) -> bytes:
    try:
        import cv2
    except ImportError as exc:
        raise SystemExit("Install camera extras: pip install -e '.[client-camera]'") from exc
    cam = cv2.VideoCapture(index)
    try:
        if not cam.isOpened():
            raise SystemExit(f"Camera {index} could not be opened")
        for _ in range(8):
            ok, frame = cam.read()
        if not ok:
            raise SystemExit("Camera returned no frame")
        ok, encoded = cv2.imencode(".jpg",frame)
        if not ok:
            raise SystemExit("JPEG encoding failed")
        return bytes(encoded)
    finally:
        cam.release()

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Connected Photobox phase 1 CLI")
    parser.add_argument("--server",default="http://127.0.0.1:8000")
    parser.add_argument("--box-id",default="pi-01")
    parser.add_argument("--timeout",type=int,default=120)
    sub = parser.add_subparsers(dest="cmd",required=True)
    sub.add_parser("health")
    sub.add_parser("camera-test").add_argument("--camera",type=int,default=0)
    cap = sub.add_parser("capture")
    cap.add_argument("--file",type=Path)
    cap.add_argument("--camera",type=int,default=0)
    cap.add_argument("--attest-documented-consent",action="store_true",help="Required: the subject gave documented consent outside this tool, or the image is synthetic")
    cap.add_argument("--pool-consent",action="store_true",help="Only when separate, documented pool authorization exists")
    cap.add_argument("--no-process",action="store_true")
    st = sub.add_parser("status");st.add_argument("session_id")
    res = sub.add_parser("result");res.add_argument("session_id");res.add_argument("--output",type=Path,default=Path("photostrip.png"))
    args = parser.parse_args(argv)
    if args.cmd=="capture" and not args.attest_documented_consent:
        parser.error("capture requires --attest-documented-consent: use it only when the subject gave documented consent outside this tool, or the image is synthetic")
    try:
        with httpx.Client(base_url=args.server,timeout=args.timeout) as client:
            if args.cmd=="health":
                response=client.get("/api/v1/health")
            elif args.cmd=="camera-test":
                payload=take_photo(args.camera)
                print(f"Camera works; JPEG bytes: {len(payload)}")
                return
            elif args.cmd=="status":
                response=client.get(f"/api/v1/sessions/{args.session_id}")
            elif args.cmd=="result":
                response=client.get(f"/api/v1/sessions/{args.session_id}/result")
                response.raise_for_status()
                args.output.write_bytes(response.content)
                print(f"Saved: {args.output}")
                return
            else:
                print("DEVELOPMENT TEST ONLY: operator attested documented consent for image processing; --pool-consent additionally attests separate pool-use permission.")
                raw=args.file.read_bytes() if args.file else take_photo(args.camera)
                response=client.post("/api/v1/sessions",json={"box_id":args.box_id});response.raise_for_status()
                sid=response.json()["id"]
                print(f"Session: {sid}")
                c=client.post(f"/api/v1/sessions/{sid}/consent",json={"processing":True,"pool":args.pool_consent,"documented_externally":args.attest_documented_consent});c.raise_for_status()
                up=client.post(f"/api/v1/sessions/{sid}/portrait",files={"image":("capture.jpg",raw,"image/jpeg")});up.raise_for_status()
                if args.no_process:
                    print("Uploaded; processing skipped.")
                    return
                response=client.post(f"/api/v1/sessions/{sid}/process")
            response.raise_for_status()
            print(response.json())
    except (httpx.HTTPError,OSError) as exc:
        print(f"Error: {exc}",file=sys.stderr)
        raise SystemExit(1)

if __name__=="__main__": main()
