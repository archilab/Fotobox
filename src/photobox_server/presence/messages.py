"""Presence message catalog and its priority rules (ADR-023, spec section 5).

Every message is chosen from the snapshot's real state; none is a guess or a random claim. Copy
lives here, not in the state code. Placeholders are filled in one pass from cleaned values, so a
box label can neither inject control characters (terminal escape codes on the kiosk) nor bring in
another placeholder. HTML escaping is the job of whichever page renders the text.
"""
from __future__ import annotations
import re
import unicodedata
from collections.abc import Mapping, Sequence
from .projection import BoxView

LANGUAGES = ("de", "en")
MAX_VALUE_LENGTH = 40

# key: (priority, {language: (hero, supporting text)})
CATALOG: dict[str, tuple[str, dict[str, tuple[str, str]]]] = {
    "unavailable": ("P0", {
        "en": ("This Photobox is temporarily unavailable.", "Please try again shortly."),
        "de": ("Diese Fotobox ist gerade nicht verfügbar.", "Bitte versuche es gleich noch einmal."),
    }),
    "live_match": ("P1", {
        "en": ("You're connected with {location}!", "Two people, one shared photo."),
        "de": ("Du bist mit {location} verbunden!", "Zwei Menschen, ein gemeinsames Foto."),
    }),
    "partner_waiting": ("P2", {
        "en": ("Someone {where} is waiting for a shared photo.", "Take yours now to join them."),
        "de": ("Jemand {where} wartet auf ein gemeinsames Foto.", "Mach jetzt deins und ihr landet auf einem Streifen."),
    }),
    "remote_active": ("P2", {
        "en": ("Someone is taking a photo {where}.", "Take yours now for a chance to connect."),
        "de": ("Jemand macht gerade ein Foto {where}.", "Mach jetzt deins, vielleicht verbindet ihr euch."),
    }),
    "recent_photo": ("P3", {
        "en": ("A photo was just taken {where}.", "Add yours to the story."),
        "de": ("Gerade wurde ein Foto {where} gemacht.", "Schreib die Geschichte mit deinem weiter."),
    }),
    "earlier_portrait": ("P4", {
        "en": ("Someone left a photo for you.", "Take yours to create an unexpected encounter."),
        "de": ("Jemand hat ein Foto für dich dagelassen.", "Mach deins für eine überraschende Begegnung."),
    }),
    "online_only": ("P5", {
        "en": ("Other Photoboxes are connected.", "Take a photo to start an encounter."),
        "de": ("Andere Fotoboxen sind verbunden.", "Mach ein Foto und starte eine Begegnung."),
    }),
    "idle": ("P6", {
        "en": ("Your next encounter starts here.", "Take a photo to begin."),
        "de": ("Deine nächste Begegnung beginnt hier.", "Mach ein Foto, um anzufangen."),
    }),
}
# A box is named by its label, or else described without any identifier.
PLACES = {
    "en": {"location": "{label}", "where": "in {label}", "location_unnamed": "another Photobox", "where_unnamed": "at another Photobox"},
    "de": {"location": "{label}", "where": "in {label}", "location_unnamed": "einer anderen Fotobox", "where_unnamed": "an einer anderen Fotobox"},
}
STATUS_WORDS = {
    "session_active": {"en": "LIVE", "de": "LIVE"},
    "recent_photo": {"en": "RECENT", "de": "GERADE EBEN"},
    "online": {"en": "ONLINE", "de": "ONLINE"},
    "offline": {"en": "OFFLINE", "de": "OFFLINE"},
    "earlier": {"en": "EARLIER", "de": "FRÜHER"},
}
# The status word shown beside each message, so the kind of encounter is never left to colour alone.
BADGES = {"live_match": "session_active", "partner_waiting": "session_active", "remote_active": "session_active", "recent_photo": "recent_photo",
          "earlier_portrait": "earlier", "online_only": "online"}
PLACEHOLDER = re.compile(r"\{(location|where|elapsed|label)\}")


def clean(value: str, limit: int = MAX_VALUE_LENGTH) -> str:
    """Drop control and format characters, collapse whitespace and limit the length."""
    kept = "".join(" " if ch.isspace() else ch for ch in value if unicodedata.category(ch)[0] != "C" or ch.isspace())
    return " ".join(kept.split())[:limit]


def fill(template: str, values: Mapping[str, str]) -> str:
    """Replace known placeholders once. Values are cleaned first, and are never scanned again."""
    safe = {key: clean(value) for key, value in values.items()}
    return PLACEHOLDER.sub(lambda match: safe.get(match.group(1), ""), template)


def place(label: str | None, language: str) -> dict[str, str]:
    words = PLACES[language]
    name = clean(label) if label else ""
    if not name:
        return {"location": words["location_unnamed"], "where": words["where_unnamed"]}
    return {"location": fill(words["location"], {"label": name}), "where": fill(words["where"], {"label": name})}


def elapsed(seconds: int, language: str) -> str:
    minutes = max(0, seconds) // 60
    if language == "de":
        return "gerade eben" if minutes == 0 else f"vor {minutes} Min."
    return "just now" if minutes == 0 else f"{minutes} min ago"


def status_word(view: BoxView) -> dict[str, str]:
    if view.device_state == "offline":
        return dict(STATUS_WORDS["offline"])
    return dict(STATUS_WORDS.get(view.activity_state, STATUS_WORDS["online"]))


def choose(*, self_unavailable: bool, locations: Sequence[BoxView], earlier_portrait: bool,
           live_partner: BoxView | None = None) -> tuple[str, BoxView | None]:
    """The single most important true message, and the box it is about. `locations` must be ordered."""
    if self_unavailable:
        return "unavailable", None
    if live_partner is not None:
        return "live_match", live_partner
    online = [view for view in locations if view.device_state == "online"]
    waiting = next((view for view in online if view.match_available), None)
    if waiting is not None:
        return "partner_waiting", waiting
    for state, key in (("session_active", "remote_active"), ("recent_photo", "recent_photo")):
        match = next((view for view in online if view.activity_state == state), None)
        if match is not None:
            return key, match
    if earlier_portrait:
        return "earlier_portrait", None
    if online:
        return "online_only", None
    return "idle", None


def render(key: str, about: BoxView | None) -> dict:
    priority, texts = CATALOG[key]
    rendered = {}
    for language in LANGUAGES:
        hero, support = texts[language]
        values = place(about.label if about else None, language)
        if about is not None and about.activity_age_seconds is not None:
            values["elapsed"] = elapsed(about.activity_age_seconds, language)
        rendered[language] = {"hero": fill(hero, values), "support": fill(support, values)}
    badge = BADGES.get(key)
    return {"priority": priority, "key": key, "texts": rendered, "badge": dict(STATUS_WORDS[badge]) if badge else None}
