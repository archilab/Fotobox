"""Frame prompts for a photo booth strip, built from editable, versioned content (ADR-019, ADR-022).

The content holds the scene template, backdrops, stagings and micro-stories. The defaults below are
version 1; the dashboard can save changed versions, and each job records the version it used. Every
version must pass `validate_content`, which keeps the rules the stories were written to: the camera
stays at eye level, both people are named, and nothing is romantic, violent or unkind about looks.
"""
from __future__ import annotations
import copy
import hashlib
import json
import random
import re
from .stories import STORIES

_SCENE = (
    "A real photo booth photograph. The camera is fixed at eye level and faces straight ahead, with a wide-angle lens and "
    "direct flash, in front of a plain seamless {backdrop} backdrop that fills the whole background. The main characters "
    "are the person from Image 1 and the person from Image 2, wearing the same clothes as in their images. Keep each "
    "person's face, age and hair exactly as in their image. Staging: {staging} This moment: {beat}"
)
# Openings vary distance and position too, but keep both faces large enough to recognize.
_OPENING_STAGINGS = [
    "{x} is close to the lens on one side, from the shoulders up, while {y} stands a step further back on the other side, both faces clear.",
    "{x} and {y} stand at clearly different distances, {y} slightly behind and higher than {x}, overlapping a little, both faces clear.",
    "{x} faces the camera in the foreground off-center, while {y} leans in from behind at an angle on the opposite side, both faces clear.",
    "{x} sits low in the foreground while {y} stands behind and to the side, heads at very different heights, both faces clear.",
]
_STAGINGS = [
    "{x} is very close to the lens, filling one side of the frame from the shoulders up, while {y} is far behind on the opposite side, small but fully visible.",
    "{x} leans in from one edge of the frame and is half cut off by the border, while {y} stands off-center in the middle distance.",
    "{x} crouches low in the foreground at the bottom of the frame, while {y} stands tall behind and to the side.",
    "{x} stands sideways in profile in the foreground, turned toward {y}, who faces the camera further back.",
    "{x} peeks over the shoulder of {y} from behind, only the head showing, while {y} is close to the lens.",
    "{x} leans diagonally across the frame toward the lens, while {y} stands upright and further back on the other side.",
    "{x} sits on the floor in the foreground, while {y} stands far behind on the opposite side.",
    "{x} is close to the lens and slightly out of focus at one edge, while {y} is sharp in the middle distance.",
    "{x} and {y} overlap strongly at different distances, the nearer one partly covering the farther one, heads at different heights.",
    "{x} has their back half turned to the camera in the foreground, looking back over their shoulder, while {y} faces the camera further back.",
    "{x} kneels in the middle distance while {y} bends down into the foreground toward the lens.",
    "{x} stands at the far edge of the frame, small and off-center, while {y} fills the opposite side close to the lens.",
]
_A, _B = "the person from Image 1", "the person from Image 2"

FORBIDDEN = re.compile(
    r"\b(kiss\w*|hug|hugs|hugging|embrac\w*|romantic|cuddl\w*|lovers?|couples?|guns?|knife|knives|swords?|blood\w*"
    r"|naked|nude|drunk|beer|wine|cigar\w*|child\w*|kids?|fat|ugly|stupid)\b",
    re.IGNORECASE,
)
CAMERA_MOVES = re.compile(r"\b(tilted|seen from above|overhead|bird's-eye|low angle|high angle|dutch angle)\b", re.IGNORECASE)
MAX_TEXT = 1200


def default_content() -> dict:
    return copy.deepcopy({
        "scene": _SCENE,
        "clear_faces": " Both faces are clearly visible.",
        "backdrops": ["mustard yellow", "teal", "coral", "pale pink"],
        "opening_stagings": _OPENING_STAGINGS,
        "stagings": _STAGINGS,
        "stories": [{"title": s[0], "beats": list(s[1:]), "enabled": True} for s in STORIES],
    })


def content_version(content: dict) -> str:
    canonical = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "stories-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


PROMPT_VERSION = content_version(default_content())
BACKDROPS: tuple[str, ...] = tuple(default_content()["backdrops"])


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"{(\w*)}", text))


def validate_content(content: dict) -> list[str]:
    """Every problem that would make this content unsafe or unusable. Empty means it may be saved."""
    errors: list[str] = []
    try:
        scene, backdrops = content["scene"], content["backdrops"]
        openings, stagings, stories = content["opening_stagings"], content["stagings"], content["stories"]
        clear = content.get("clear_faces", "")
    except (KeyError, TypeError):
        return ["the content is missing a part"]
    if _placeholders(scene) != {"backdrop", "staging", "beat"}:
        errors.append("the scene must contain {backdrop}, {staging} and {beat}, and nothing else in braces")
    if "eye level" not in scene:
        errors.append("the scene must keep the camera fixed at eye level")
    if not backdrops or any(not b.strip() or len(b) > 40 or _placeholders(b) for b in backdrops):
        errors.append("there must be at least one backdrop, each a short color without braces")
    if not openings:
        errors.append("there must be at least one opening staging")
    if len(stagings) < 3:
        errors.append("there must be at least three stagings, one for each frame after the first")
    for kind, items in (("opening staging", openings), ("staging", stagings)):
        for number, text in enumerate(items, start=1):
            if _placeholders(text) != {"x", "y"}:
                errors.append(f"{kind} {number} must name both people as {{x}} and {{y}}")
    if not [s for s in stories if s.get("enabled", True)]:
        errors.append("at least one story must be enabled")
    titles = [s.get("title", "").strip().lower() for s in stories]
    if len(set(titles)) != len(titles) or "" in titles:
        errors.append("every story needs a unique title")
    for story in stories:
        title, beats = story.get("title", "?"), story.get("beats", [])
        if len(beats) != 4:
            errors.append(f"story '{title}' needs exactly four beats")
            continue
        if not ("{a}" in beats[0] and "{b}" in beats[0]):
            errors.append(f"story '{title}': the opening beat must name both {{a}} and {{b}}")
        for number, beat in enumerate(beats, start=1):
            if not _placeholders(beat) <= {"a", "b"}:
                errors.append(f"story '{title}', beat {number}: only {{a}} and {{b}} may appear in braces")
            if not re.search(r"{[ab]}|\b(both|them)\b", beat, re.IGNORECASE):
                errors.append(f"story '{title}', beat {number}: must refer to the people")
    for text in [scene, clear, *backdrops, *openings, *stagings, *(b for s in stories for b in s.get("beats", []))]:
        if len(text) > MAX_TEXT:
            errors.append("a text is longer than 1200 characters")
        if FORBIDDEN.search(text):
            errors.append(f"not suitable for strangers: '{FORBIDDEN.search(text).group(0)}'")
        if CAMERA_MOVES.search(text):
            errors.append(f"the camera must not move: '{CAMERA_MOVES.search(text).group(0)}'")
    return list(dict.fromkeys(errors))


def prompts_for(frame_count: int, seed: int, content: dict | None = None) -> list[str]:
    """One prompt per frame. Story, backdrop and staging follow from the job's seed, so a resumed job keeps them."""
    content = content or default_content()
    backdrops = content["backdrops"]
    stories = [s for s in content["stories"] if s.get("enabled", True)]
    backdrop = backdrops[seed % len(backdrops)]
    opening, development, escalation, payoff = stories[(seed // len(backdrops)) % len(stories)]["beats"]
    clear = content.get("clear_faces", "")
    beats = [opening + clear, development, payoff] if frame_count == 3 else [opening + clear, development, escalation, payoff]
    chooser = random.Random(seed)
    stagings = [chooser.choice(content["opening_stagings"])] + chooser.sample(content["stagings"], len(beats) - 1)
    prompts = []
    for staging, beat in zip(stagings, beats):
        # Either person can be the one nearer the lens.
        x, y = (_A, _B) if chooser.random() < 0.5 else (_B, _A)
        prompts.append(content["scene"].format(backdrop=backdrop, staging=staging.format(x=x, y=y), beat=beat.format(a=_A, b=_B)))
    return prompts
