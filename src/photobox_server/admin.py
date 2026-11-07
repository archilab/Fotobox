"""Local administration, run on the server machine. These actions have no HTTP endpoint."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
from .auth import create_device, revoke_device
from .config import load_env_file
from .core import cleanup_expired, db_connect
from .pool import import_seed, remove_seed
from .withdrawal import session_for_code, withdraw_session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Connected Photobox local administration")
    commands = parser.add_subparsers(dest="cmd", required=True)
    device = commands.add_parser("device", help="manage device tokens")
    actions = device.add_subparsers(dest="action", required=True)
    actions.add_parser("add", help="register a device and print its token once").add_argument("device_id")
    actions.add_parser("revoke", help="revoke a device's token").add_argument("device_id")
    actions.add_parser("list", help="list devices (tokens are not shown)")
    commands.add_parser("cleanup", help="delete expired portraits, their strips and untracked files now")
    seed = commands.add_parser("seed", help="manage rights-cleared seed images")
    seed_actions = seed.add_subparsers(dest="action", required=True)
    seed_add = seed_actions.add_parser("add", help="import a seed image")
    seed_add.add_argument("file", type=Path)
    seed_add.add_argument("--rights-reference", required=True, help="Licence or permission reference, or SYNTHETIC. Never personal data.")
    seed_add.add_argument("--attest-rights", action="store_true", help="Required: you hold the rights and permission to use this image as a seed")
    seed_actions.add_parser("remove", help="delete a seed and every strip containing it").add_argument("seed_id")
    seed_actions.add_parser("list", help="list seeds")
    withdraw = commands.add_parser("withdraw", help="withdraw consent and delete a session's images and strips")
    withdraw.add_argument("--session", help="Session id; without it, the participant's code is asked for")
    log = commands.add_parser("audit", help="show recent audit events")
    log.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    if args.cmd == "seed" and args.action == "add" and not args.attest_rights:
        parser.error("seed add requires --attest-rights: use it only when you hold the rights and permission to use this image")

    load_env_file()
    code = None
    if args.cmd == "withdraw" and not args.session:
        # Asked for here rather than as an argument, which other accounts could see.
        code = input("Withdrawal code: ").strip()
    with db_connect() as db:
        if args.cmd == "seed":
            return run_seed(db, args)
        if args.cmd == "withdraw":
            return run_withdraw(db, args.session or session_for_code(db, code))
        if args.cmd == "audit":
            for row in reversed(db.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (args.limit,)).fetchall()):
                print("\t".join(str(row[k] or "-") for k in ("occurred_at", "event", "session_id", "portrait_id", "device_id", "detail")))
            return 0
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


def run_seed(db, args: argparse.Namespace) -> int:
    if args.action == "add":
        try:
            print(f"Seed imported: {import_seed(db, args.file.read_bytes(), args.rights_reference)}")
        except (ValueError, OSError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    elif args.action == "remove":
        if not remove_seed(db, args.seed_id):
            print(f"Error: no active seed {args.seed_id!r}", file=sys.stderr)
            return 1
        print(f"Seed {args.seed_id} removed.")
    else:
        for row in db.execute("SELECT id,rights_ref,imported_at,use_count FROM seeds WHERE removed_at IS NULL ORDER BY imported_at"):
            print(f"{row['id']}\trights {row['rights_ref']}\timported {row['imported_at']}\tused {row['use_count']}")
    return 0


def run_withdraw(db, session_id: str | None) -> int:
    result = withdraw_session(db, session_id) if session_id else None
    if result is None:
        print("Error: no session found for this code or id", file=sys.stderr)
        return 1
    if result.already_withdrawn:
        print(f"Session {result.session_id} was already withdrawn.")
    print(f"Consent withdrawn for session {result.session_id}. Portraits deleted: {result.portraits}. Strips deleted: {result.strips}.")
    print("Copies outside the server, such as a strip saved on a client or a backup, are not covered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
