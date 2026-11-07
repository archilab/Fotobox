"""WiFi through NetworkManager's `nmcli` (ADR-021).

The box's account may change network settings through a narrow polkit rule (deploy/pi). Passwords are
handed to nmcli on its standard input, never as a command-line argument, which other accounts could see
in the process list, and they are never logged.
"""
from __future__ import annotations
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

Runner = Callable[..., subprocess.CompletedProcess]
CONNECTION_PREFIX = "photobox "


@dataclass(frozen=True)
class Network:
    ssid: str
    signal: int
    secured: bool
    in_use: bool


def split_terse(line: str) -> list[str]:
    """Split nmcli's terse output, where a literal ':' or '\\' is escaped with a backslash."""
    fields, current, escaped = [], [], False
    for character in line:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(character)
    fields.append("".join(current))
    return fields


class Wifi:
    def __init__(self, runner: Runner = subprocess.run, interface: str = "wlan0") -> None:
        self.runner, self.interface = runner, interface

    def _nmcli(self, *arguments: str, stdin: str | None = None, timeout: int = 45) -> subprocess.CompletedProcess:
        return self.runner(["nmcli", *arguments], input=stdin, capture_output=True, text=True, timeout=timeout)

    def current(self) -> str | None:
        """The connected network's name, or None."""
        result = self._nmcli("-t", "-f", "ACTIVE,SSID", "device", "wifi", "list", "--rescan", "no")
        for line in result.stdout.splitlines():
            active, *rest = split_terse(line)
            if active == "yes" and rest and rest[0]:
                return rest[0]
        return None

    def online(self) -> bool:
        result = self._nmcli("-t", "-f", "STATE", "general")
        return result.returncode == 0 and result.stdout.strip().startswith("connected")

    def scan(self) -> list[Network]:
        result = self._nmcli("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes")
        best: dict[str, Network] = {}
        for line in result.stdout.splitlines():
            fields = split_terse(line)
            if len(fields) < 4 or not fields[1]:
                continue  # hidden networks have no name to show
            in_use, ssid, signal, security = fields[0] == "*", fields[1], int(fields[2] or 0), fields[3]
            network = Network(ssid, signal, security not in ("", "--"), in_use)
            if ssid not in best or network.signal > best[ssid].signal or in_use:
                best[ssid] = network
        return sorted(best.values(), key=lambda n: (not n.in_use, -n.signal, n.ssid))

    def connect(self, ssid: str, password: str | None) -> tuple[bool, str]:
        """Save and activate a network. On failure the new profile is removed and the old network returns."""
        if "\n" in ssid or (password and "\n" in password):
            return False, "invalid characters"
        if password is not None and not 8 <= len(password) <= 63:
            return False, "WiFi passwords have 8 to 63 characters"
        name = CONNECTION_PREFIX + re.sub(r"[^\w .-]", "_", ssid)[:40]
        self._nmcli("connection", "delete", "id", name)
        added = self._nmcli("connection", "add", "type", "wifi", "con-name", name, "ifname", self.interface, "ssid", ssid,
                            *(("wifi-sec.key-mgmt", "wpa-psk") if password else ()))
        if added.returncode != 0:
            return False, "could not save the network"
        if password:
            # The interactive editor reads the password from standard input, so it never appears as an argument.
            edited = self._nmcli("connection", "edit", "id", name, stdin=f"set wifi-sec.psk {password}\nsave persistent\nquit\n")
            if edited.returncode != 0:
                self._nmcli("connection", "delete", "id", name)
                return False, "could not save the password"
        up = self._nmcli("--wait", "30", "connection", "up", "id", name, timeout=60)
        if up.returncode != 0:
            self._nmcli("connection", "delete", "id", name)
            return False, "could not connect: check the password and signal"
        return True, "connected"
