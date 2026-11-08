"""What each kiosk screen shows, as data (ADR-028, milestone U1).

The kiosk describes a screen as a plain, JSON-ready state: `screen` names the view in ui/screens.js, the
other fields are its content, with real umlauts. `plain` is the same screen as console text, so the text
kiosk keeps working as the fallback when no browser is available. Nothing here decides what happens next.
"""
from __future__ import annotations

ICONS = {"live": "●", "recent": "◔", "online": "○", "offline": "×"}
LIVE_KEYS = {"live_match", "partner_waiting", "remote_active"}
BADGE_KINDS = {"recent_photo": "recent", "earlier_portrait": "earlier", "online_only": "online"}
IDLE_HERO = {"de": "Deine nächste Begegnung beginnt hier.", "en": "Your next encounter starts here."}
IDLE_SUPPORT = {"de": "Mach ein Foto, um anzufangen.", "en": "Take a photo to begin."}


def present(screen, state: dict) -> None:
    """Hand a state to a screen that understands states, or else show its console text."""
    view = getattr(screen, "view", None)
    if view is not None:
        view(state)
    else:
        screen.show(state["plain"])


def _tile(location: dict) -> dict:
    if location.get("device_state") == "offline":
        kind = "offline"
    else:
        kind = {"session_active": "live", "recent_photo": "recent"}.get(location.get("activity_state"), "online")
    word = (location.get("status") or {}).get("de", "")
    return {"kind": kind, "label": location.get("label") or "Fotobox", "status": f"{ICONS[kind]} {word}".strip()}


def start(snapshot: dict | None, plain: str, max_tiles: int = 3) -> dict:
    state = {"screen": "start", "hero": dict(IDLE_HERO), "support": dict(IDLE_SUPPORT), "tiles": [], "badge": None, "plain": plain}
    message = (snapshot or {}).get("message") or {}
    texts = message.get("texts") or {}
    if "de" in texts and "en" in texts:
        state["hero"] = {"de": texts["de"]["hero"], "en": texts["en"]["hero"]}
        state["support"] = {"de": texts["de"]["support"], "en": texts["en"]["support"]}
    if message.get("badge"):
        kind = "live" if message.get("key") in LIVE_KEYS else BADGE_KINDS.get(message.get("key"), "")
        state["badge"] = {"kind": kind, **message["badge"]}
    state["tiles"] = [_tile(item) for item in (snapshot or {}).get("locations", [])[:max_tiles]]
    label = ((snapshot or {}).get("self") or {}).get("label")
    if label:
        state["label"] = label.upper()
    return state


def consent(text: str) -> dict:
    return {"screen": "consent", "text": text, "plain": text}


def pool(question: dict) -> dict:
    return {"screen": "pool", "text": question["text"], "until": question["until_text"], "plain": question["text"]}


def message(text: str, *, key: bool = False) -> dict:
    """`text` is German, a line break, then English."""
    de, _, en = text.partition("\n")
    return {"screen": "message", "de": de, "en": en, "key": key, "plain": text + ("\n\nENTER" if key else "")}


def countdown(number: int, plain: str) -> dict:
    return {"screen": "countdown", "number": number, "plain": plain}


def flash() -> dict:
    return {"screen": "flash", "plain": "\n\n        * KLICK *"}


def _place(label: str | None) -> tuple[str, str]:
    return (f"in {label}", f"in {label}") if label else ("an einer anderen Fotobox", "at another photo booth")


def encounter(status: dict, waited: bool, talk: dict | None = None) -> dict | None:
    """Who this visitor's photo meets, as the server reports it. None for a seed nobody waited for."""
    job = status.get("job") or {}
    match = status.get("match") or {}
    if job.get("waiting_for_partner"):
        seconds = job.get("wait_seconds_left")
        if job.get("partner_coming"):
            de, en = _place(job.get("partner_coming_label"))
            return {"kind": "searching", "de": f"Jemand {de} macht auch gerade ein Foto.", "en": f"Someone {en} is taking a photo too.", "seconds": seconds}
        return {"kind": "searching", "de": "Wir suchen jemanden an einer anderen Fotobox", "en": "Looking for someone at another photo booth", "seconds": seconds}
    if match.get("kind") == "live":
        label = match.get("partner_label")
        view = {"kind": "live", "de": f"Verbunden mit {label or 'einer anderen Fotobox'}", "en": f"Connected to {label or 'another photo booth'}"}
        return view | (talk or {})
    if match.get("kind") == "pool":
        return {"kind": "other", "de": "Dein Foto trifft jemanden, der vorhin hier war.", "en": "Your photo meets someone who was here earlier."}
    if waited and match.get("kind") == "seed":
        return {"kind": "other", "de": "Gerade war niemand an einer anderen Fotobox.", "en": "Nobody was at another photo booth just now."}
    return None


def making(elapsed: int, job: dict, encounter_view: dict | None, plain: str) -> dict:
    frames = {"done": job.get("frames_done", 0), "total": job["frames_total"]} if job.get("frames_total") else None
    return {"screen": "making", "clock": f"{elapsed // 60:02d}:{elapsed % 60:02d}", "frames": frames, "encounter": encounter_view, "plain": plain}


def live_moment(label: str | None, plain: str) -> dict:
    de, en = _place(label)
    return {"screen": "live_moment", "de": f"Du und jemand {de}: ein gemeinsamer Fotostreifen!", "en": f"You and someone {en}: one shared photo strip!", "plain": plain}


def strip(image: str, code: str | None) -> dict:
    return {"screen": "strip", "image": image, "code": code, "plain": ""}


def done(code: str | None, pool_until: str | None, plain: str) -> dict:
    return {"screen": "done", "code": code, "pool": pool_until, "plain": plain}


def listening(seconds: int, limit: int, plain: str) -> dict:
    return {"screen": "listening", "seconds": seconds, "limit": limit, "plain": plain}


def converting(plain: str) -> dict:
    return {"screen": "converting", "plain": plain}
