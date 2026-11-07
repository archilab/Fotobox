"""Local administration, run on the server machine. These actions have no HTTP endpoint."""
from __future__ import annotations
import argparse
import sys
from .auth import create_device, revoke_device
from .config import load_env_file
from .core import cleanup_expired, db_connect


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Connected Photobox local administration")
    commands = parser.add_subparsers(dest="cmd", required=True)
    device = commands.add_parser("device", help="manage device tokens")
    actions = device.add_subparsers(dest="action", required=True)
    actions.add_parser("add", help="register a device and print its token once").add_argument("device_id")
    actions.add_parser("revoke", help="revoke a device's token").add_argument("device_id")
    actions.add_parser("list", help="list devices (tokens are not shown)")
    commands.add_parser("cleanup", help="delete expired portraits, their strips and untracked files now")
    args = parser.parse_args(argv)

    load_env_file()
    with db_connect() as db:
        if args.cmd == "cleanup":
            result = cleanup_expired(db)
            print(f"Expired portraits deleted: {result.portraits}")
            print(f"Strips deleted: {result.strips}")
            print(f"Untracked files deleted: {result.untracked_files}")
        elif args.action == "add":
            try:
                token = create_device(db, args.device_id)
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            print(f"Device {args.device_id} registered. This token is shown only once:")
            print(token)
        elif args.action == "revoke":
            if not revoke_device(db, args.device_id):
                print(f"Error: no active device {args.device_id!r}", file=sys.stderr)
                return 1
            print(f"Device {args.device_id} revoked.")
        else:
            for row in db.execute("SELECT id,created_at,revoked_at FROM devices ORDER BY id"):
                print(f"{row['id']}\tcreated {row['created_at']}\t{'revoked ' + row['revoked_at'] if row['revoked_at'] else 'active'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
