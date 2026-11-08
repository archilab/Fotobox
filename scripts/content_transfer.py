#!/usr/bin/env python3
"""Move what an operator made to another server: seed images, notices, stories and blocked words.

    python scripts/content_transfer.py export FOLDER     # on the old server, in its folder
    python scripts/content_transfer.py import FOLDER     # on the new server, in its folder

Never touches visitors' photos, strips, sessions, consent records, the audit trail or devices. Those stay
on the old server until they expire. Importing does not overwrite anything already there.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from photobox_server.config import load_env_file
from photobox_server.core import db_connect, storage_root

MANIFEST = "content.json"
TABLES = {
    "consent_notices": ("version", "text", "created_at", "active", "kind"),
    "prompt_sets": ("version", "content", "created_at", "active"),
    "message_filter": ("id", "words", "updated_at"),
}
SEED_COLUMNS = ("id", "rights_ref", "imported_at", "use_count")


def export_content(folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    data: dict = {}
    with db_connect() as db:
        for table, columns in TABLES.items():
            data[table] = [dict(zip(columns, row)) for row in db.execute(f"SELECT {','.join(columns)} FROM {table}")]
        seeds = db.execute(f"SELECT path,{','.join(SEED_COLUMNS)} FROM seeds WHERE removed_at IS NULL").fetchall()
    data["seeds"] = []
    for seed in seeds:
        source = Path(seed["path"])
        name = f"seed-{seed['id']}{source.suffix}"
        shutil.copyfile(source, folder / name)
        (folder / name).chmod(0o600)
        data["seeds"].append({column: seed[column] for column in SEED_COLUMNS} | {"file": name})
    (folder / MANIFEST).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {key: len(value) for key, value in data.items()}


def import_content(folder: Path) -> dict:
    data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    added = {}
    target = storage_root() / "seed_portraits"
    target.mkdir(parents=True, exist_ok=True)
    os.chmod(target, 0o700)
    with db_connect() as db:
        for table, columns in TABLES.items():
            added[table] = 0
            for row in data.get(table, []):
                cursor = db.execute(f"INSERT OR IGNORE INTO {table}({','.join(columns)}) VALUES({','.join('?' * len(columns))})", [row[c] for c in columns])
                added[table] += cursor.rowcount
        added["seeds"] = 0
        for seed in data.get("seeds", []):
            name = Path(seed["file"]).name  # never a path out of the folder
            destination = target / f"{seed['id']}{Path(name).suffix}"
            cursor = db.execute("INSERT OR IGNORE INTO seeds(id,path,rights_ref,imported_at,use_count) VALUES(?,?,?,?,?)",
                                (seed["id"], str(destination), seed["rights_ref"], seed["imported_at"], seed["use_count"]))
            if cursor.rowcount:
                shutil.copyfile(folder / name, destination)
                destination.chmod(0o600)
                added["seeds"] += 1
    return added


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("export", "import"):
        print(__doc__)
        return 2
    load_env_file()
    result = (export_content if sys.argv[1] == "export" else import_content)(Path(sys.argv[2]))
    print(sys.argv[1], result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
