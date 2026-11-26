"""The QR code on the resting screen (ADR-041), drawn on the booth, so nothing is loaded from outside.

The booth hands the browser the code's modules, one string of "1" (dark) and "0" per row, and the browser
draws them; the code is made once per address. Without the `client-screen` extra (segno), there is no code
and the resting screen shows the address as text only.
"""
from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlsplit

DEFAULT_ADDRESS = "https://www.midap.space"
MAX_ADDRESS = 200


def valid_address(text: str) -> str | None:
    """An http(s) address with a host, or None."""
    address = text.strip()
    if not address or len(address) > MAX_ADDRESS or any(c.isspace() for c in address):
        return None
    parts = urlsplit(address)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return address


def shown_address(address: str) -> str:
    """What visitors read under the code: 'midap.space' for https://www.midap.space/."""
    parts = urlsplit(address)
    host = parts.hostname or address
    host = host[4:] if host.startswith("www.") else host
    path = parts.path.rstrip("/")
    return host + path


@lru_cache(maxsize=4)
def modules(address: str) -> tuple[str, ...] | None:
    """The code's rows, without the quiet zone the screen draws around it; None without segno."""
    try:
        import segno
    except ImportError:
        return None
    # Level M: still readable with a smudge or glare on the panel, and small enough for a short address.
    code = segno.make(address, error="m", micro=False, boost_error=False)
    return tuple("".join("1" if module else "0" for module in row) for row in code.matrix)
