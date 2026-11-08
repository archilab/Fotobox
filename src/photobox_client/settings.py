"""The box's own settings and the setup menu's PIN (ADR-021).

Settings are KEY=VALUE lines, so the file stays readable and editable by hand. The PIN is stored only as
a salted, slow hash. Both files are readable only by the box's account.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

KEYS = ("PHOTOBOX_SERVER", "PHOTOBOX_CAMERA")
PIN_ITERATIONS = 200_000
PIN_LENGTHS = range(4, 9)
FREE_PIN_ATTEMPTS = 3


def _write_private(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, path)


@dataclass
class Settings:
    server: str = ""
    camera: str = "0"
    microphone: str = ""
    person_detection: bool = False  # off unless the operator switches it on (ADR-029)
    person_area: str = ""  # left,top,right,bottom as fractions; empty is the whole picture

    @classmethod
    def load(cls, path: Path) -> "Settings":
        values: dict[str, str] = {}
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition("=")
                if separator and not key.strip().startswith("#"):
                    values[key.strip()] = value.strip()
        return cls(server=values.get("PHOTOBOX_SERVER", ""), camera=values.get("PHOTOBOX_CAMERA", "0") or "0",
                   microphone=values.get("PHOTOBOX_MICROPHONE", ""),
                   person_detection=values.get("PHOTOBOX_PERSON_DETECTION", "").lower() in {"1", "true", "yes"},
                   person_area=values.get("PHOTOBOX_PERSON_AREA", ""))

    def save(self, path: Path) -> None:
        _write_private(path, f"PHOTOBOX_SERVER={self.server}\nPHOTOBOX_CAMERA={self.camera}\nPHOTOBOX_MICROPHONE={self.microphone}\n"
                                f"PHOTOBOX_PERSON_DETECTION={'true' if self.person_detection else 'false'}\nPHOTOBOX_PERSON_AREA={self.person_area}\n")


def normalize_server(text: str) -> str:
    """Accept '192.168.1.24' or 'mac.local:8880' as well as a full address."""
    address = text.strip()
    if not address:
        raise ValueError("enter the server's address")
    if "://" not in address:
        address = "http://" + address
    scheme, _, rest = address.partition("://")
    rest = rest.rstrip("/")
    address = f"{scheme}://{rest}"
    if scheme not in ("http", "https") or not rest or " " in rest:
        raise ValueError("the address should look like 192.168.1.24 or http://192.168.1.24:8880")
    host = rest.split("/", 1)[0]
    if ":" not in host.rsplit("]", 1)[-1]:
        address = f"{scheme}://{host}:8880" + rest[len(host):]
    return address


def save_token(path: Path, token: str) -> None:
    _write_private(path, token.strip() + "\n")


class Pin:
    """The setup menu's PIN. Repeated wrong entries make the box wait before the next try."""

    def __init__(self, path: Path, clock=time.monotonic) -> None:
        self.path, self.clock = path, clock
        self.failures = 0
        self.locked_until = 0.0

    def is_set(self) -> bool:
        return self.path.is_file()

    @staticmethod
    def valid(pin: str) -> bool:
        return pin.isdigit() and len(pin) in PIN_LENGTHS

    def set(self, pin: str) -> None:
        if not self.valid(pin):
            raise ValueError("the PIN must be 4 to 8 digits")
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, PIN_ITERATIONS)
        _write_private(self.path, json.dumps({"salt": salt.hex(), "hash": digest.hex(), "iterations": PIN_ITERATIONS}))

    def wait_seconds(self) -> int:
        return max(0, int(self.locked_until - self.clock() + 0.999))

    def check(self, pin: str) -> bool:
        if self.wait_seconds() > 0:
            return False
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(stored["salt"]), stored["iterations"])
        if hmac.compare_digest(digest.hex(), stored["hash"]):
            self.failures = 0
            return True
        self.failures += 1
        if self.failures >= FREE_PIN_ATTEMPTS:
            # 30 s after the third wrong PIN, doubling after each further one, up to 15 minutes.
            self.locked_until = self.clock() + min(900, 30 * 2 ** (self.failures - FREE_PIN_ATTEMPTS))
        return False
