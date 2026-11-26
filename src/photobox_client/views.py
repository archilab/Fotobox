"""What each kiosk screen shows, as data (ADR-028, milestone U1).

The kiosk describes a screen as a plain, JSON-ready state: `screen` names the view in ui/screens.js, the
other fields are its content, with real umlauts. `plain` is the same screen as console text, so the text
kiosk keeps working as the fallback when no browser is available. Nothing here decides what happens next.
"""
from __future__ import annotations
import math
import re
from datetime import datetime

LIVE_KEYS = {"live_match", "partner_waiting", "remote_active"}
BADGE_KINDS = {"person_nearby": "nearby", "recent_photo": "recent", "earlier_portrait": "earlier", "online_only": "online"}
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
        kind = {"session_active": "live", "person_nearby": "nearby", "recent_photo": "recent"}.get(location.get("activity_state"), "online")
    word = (location.get("status") or {}).get("de", "")
    # The status word alone: the screen draws the place as a line, so no symbols stand in for it (ADR-039).
    return {"kind": kind, "label": location.get("label") or "Fotobox", "word": word}


CAMERA_NOTE = {"de": "Die Kamera erkennt nur, ob jemand da ist, ohne Bild.",
               "en": "The camera only notices someone is here, no image kept."}


def invitation_seconds(snapshot: dict | None) -> int:
    """Seconds left to join a shared strip at another booth, or 0 (ADR-037).

    Both times come from the server's clock, so the box's own clock does not matter.
    """
    message = (snapshot or {}).get("message") or {}
    if message.get("key") != "partner_waiting" or not message.get("until") or not (snapshot or {}).get("generated_at"):
        return 0
    try:
        left = (datetime.fromisoformat(message["until"]) - datetime.fromisoformat(snapshot["generated_at"])).total_seconds()
    except (TypeError, ValueError):
        return 0
    return max(0, math.ceil(left))


def start(snapshot: dict | None, plain: str, max_tiles: int = 3, *, detecting: bool = False) -> dict:
    """`detecting` says the camera is looking right now, which visitors must be able to read (ADR-029).

    While another booth invites this one to a shared strip, the start screen becomes the big invitation.
    """
    seconds = invitation_seconds(snapshot)
    if seconds:
        texts = snapshot["message"]["texts"]
        label = ((snapshot.get("self") or {}).get("label") or "").upper() or None
        inviting = next((loc for loc in snapshot.get("locations") or [] if loc.get("invite_until")), None)
        place = inviting.get("label") if inviting else None
        # One sentence: the other place and the invitation (ADR-040); the server's text when the place is unknown.
        hero_text = {"de": f"{place} wartet auf dich.", "en": f"{place} is waiting for you."} if place else \
            {"de": texts["de"]["hero"], "en": texts["en"]["hero"]}
        state = {"screen": "invite", "place": place, "hero": hero_text,
                 "support": {"de": texts["de"]["support"], "en": texts["en"]["support"]}, "seconds": seconds,
                 "plain": f"{texts['de']['hero']}\n{texts['en']['hero']}\n\n{texts['de']['support']}\n{texts['en']['support']}"
                          f"\n\n        Noch {seconds} s / {seconds} s left\n\nENTER = Mitmachen / Join"}
        return state | ({"label": label} if label else {})
    state = {"screen": "start", "hero": dict(IDLE_HERO), "support": dict(IDLE_SUPPORT), "tiles": [], "badge": None, "camera_note": None, "plain": plain}
    if detecting:
        state["camera_note"] = dict(CAMERA_NOTE)
        state["plain"] = f"{plain}\n\n{CAMERA_NOTE['de'].replace('ü', 'ue')}\n{CAMERA_NOTE['en']}"
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


KEY_LINE = re.compile(r"^\s*(ENTER|ESC)\s*=", re.IGNORECASE)
AGREE = "ENTER = Ich stimme zu, Foto machen / I agree, take the photo"
AGREE_WITH_POOL = "ENTER = Ich stimme zu: Foto und Pool / I agree: photo and pool"


def _without_key_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not KEY_LINE.match(line)).strip()


# The resting screen (ADR-041): the project behind the booth. The partner logos and the funding notice are
# not shown yet: their files and wording have to come from the project, and nothing stands in their place.
PROJECT_NAME = "Multisensory in Dialogue and Artistic Practice"
COOPERATION = {"de": "Ein Kooperationsprojekt der Universität der Künste Berlin und der Folkwang Universität der Künste",
               "en": "A cooperation of the Berlin University of the Arts and Folkwang University of the Arts"}
_PLAIN = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ü": "Ue", "ß": "ss"})  # for the text screen
WAKE = {"de": "Drück den Knopf", "en": "Press the button"}


def someone_live(snapshot: dict | None) -> bool:
    """The server reports someone live at another booth, as the start screen's light shows (ADR-036)."""
    return ((snapshot or {}).get("message") or {}).get("key") in LIVE_KEYS


def resting(live: bool = False, address: str | None = None) -> dict:
    """`live` only changes the light: violet while someone is live at another booth.

    `address`: the project's website, shown as a QR code when the booth can draw one, and always as text.
    """
    from . import qr
    state = {"screen": "resting", "name": PROJECT_NAME, "cooperation": dict(COOPERATION), "wake": dict(WAKE), "live": live}
    lines = f"MIDAP\n{PROJECT_NAME}\n\n{COOPERATION['de']}\n{COOPERATION['en']}"
    if address:
        state["address"] = {"text": qr.shown_address(address), "de": "Mehr über das Projekt", "en": "More about the project",
                            "modules": qr.modules(address)}
        lines += f"\n\n{state['address']['text']}"
    state["plain"] = f"{lines}\n\nENTER = Aufwecken / Wake up".translate(_PLAIN)
    return state


def consent(text: str, pool_question: dict | None = None, seconds: int = 45) -> dict:
    """The one question a visitor answers, with one key: the notice and, if offered, the pool (ADR-030).

    The operator's own lines about keys are replaced, because only ENTER counts and doing nothing cancels.
    """
    body = _without_key_lines(text)
    if pool_question is not None:
        body = f"{body}\n\n{_without_key_lines(pool_question['text'])}"
    combined = f"{body}\n\n{AGREE_WITH_POOL if pool_question is not None else AGREE}"
    return {"screen": "consent", "text": combined, "until": pool_question["until_text"] if pool_question is not None else None, "seconds": seconds,
            "plain": f"{combined}\n\nOhne ENTER endet dies nach {seconds} Sekunden. / Without ENTER this ends after {seconds} seconds."}


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
        view = {"kind": "live", "place": label, "de": f"Verbunden mit {label or 'einer anderen Fotobox'}", "en": f"Connected to {label or 'another photo booth'}"}
        return view | (talk or {})
    if match.get("kind") == "pool":
        return {"kind": "other", "de": "Dein Foto trifft jemanden, der vorhin hier war.", "en": "Your photo meets someone who was here earlier."}
    if waited and match.get("kind") == "seed":
        return {"kind": "other", "de": "Gerade war niemand an einer anderen Fotobox.", "en": "Nobody was at another photo booth just now."}
    return None


def remaining_text(seconds: float | None, frames_done: int = 0) -> str:
    """The time left in words. Without an estimate yet, what is happening instead (ADR-040)."""
    if seconds is None:  # no estimate: say what is happening, never claim a time
        return "Das erste Bild entsteht." if frames_done == 0 else "Die Bilder entstehen."
    if seconds <= 15:
        return "Gleich fertig."
    if seconds < 50:
        return "Noch etwa eine halbe Minute."
    minutes = round(seconds / 60)
    return "Noch etwa eine Minute." if minutes <= 1 else f"Noch etwa {minutes} Minuten."


def making(elapsed: int, job: dict, encounter_view: dict | None, plain: str, remaining: float | None = None) -> dict:
    frames = {"done": job.get("frames_done", 0), "total": job["frames_total"]} if job.get("frames_total") else None
    return {"screen": "making", "clock": f"{elapsed // 60:02d}:{elapsed % 60:02d}", "frames": frames, "encounter": encounter_view,
            "remaining": remaining_text(remaining, (frames or {}).get("done", 0)), "plain": plain}


def live_moment(label: str | None, plain: str) -> dict:
    de, en = _place(label)
    return {"screen": "live_moment", "place": label, "de": f"Du und jemand {de}: ein gemeinsamer Fotostreifen!", "en": f"You and someone {en}: one shared photo strip!", "plain": plain}


def strip(image: str, code: str | None) -> dict:
    return {"screen": "strip", "image": image, "code": code, "plain": ""}


def done(code: str | None, pool_until: str | None, plain: str, seconds: int = 10) -> dict:
    return {"screen": "done", "code": code, "pool": pool_until, "seconds": seconds, "plain": plain}


def listening(seconds: int, limit: int, plain: str) -> dict:
    return {"screen": "listening", "seconds": seconds, "limit": limit, "plain": plain}


def converting(plain: str) -> dict:
    return {"screen": "converting", "plain": plain}
