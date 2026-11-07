"""Frame prompts for a photo booth strip. Versioned, because each job records which it used.

Each strip tells one randomly chosen micro-story (see stories.py) on one backdrop. The camera never
moves: it stays at eye level. Variety comes from staging instead, with each frame getting its own
arrangement of distance to the lens, foreground and background, body position and asymmetry, never
repeated within a strip. The two participants stay the main characters, but others may appear. The
opening frame keeps both faces clearly visible, so both people are recognizable at least once.
"""
from __future__ import annotations
import random
from .stories import STORIES

PROMPT_VERSION = "booth-stories-4"
BACKDROPS: tuple[str, ...] = ("mustard yellow", "teal", "coral", "pale pink")
_SCENE = (
    "A real photo booth photograph. The camera is fixed at eye level and faces straight ahead, with a wide-angle lens and "
    "direct flash, in front of a plain seamless {backdrop} backdrop that fills the whole background. The main characters "
    "are the person from Image 1 and the person from Image 2, wearing the same clothes as in their images. Keep each "
    "person's face, age and hair exactly as in their image. Staging: {staging} This moment: {beat}"
)
# Openings vary distance and position too, but keep both faces large enough to recognize.
_OPENING_STAGINGS: tuple[str, ...] = (
    "{x} is close to the lens on one side, from the shoulders up, while {y} stands a step further back on the other side, both faces clear.",
    "{x} and {y} stand at clearly different distances, {y} slightly behind and higher than {x}, overlapping a little, both faces clear.",
    "{x} faces the camera in the foreground off-center, while {y} leans in from behind at an angle on the opposite side, both faces clear.",
    "{x} sits low in the foreground while {y} stands behind and to the side, heads at very different heights, both faces clear.",
)
_STAGINGS: tuple[str, ...] = (
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
)
_A, _B = "the person from Image 1", "the person from Image 2"
_CLEAR = " Both faces are clearly visible."


def prompts_for(frame_count: int, seed: int) -> list[str]:
    """Return one prompt per frame. Story, backdrop and staging follow from the job's random seed, so a resumed job keeps them."""
    backdrop = BACKDROPS[seed % len(BACKDROPS)]
    _title, opening, development, escalation, payoff = STORIES[(seed // len(BACKDROPS)) % len(STORIES)]
    beats = [opening + _CLEAR, development, payoff] if frame_count == 3 else [opening + _CLEAR, development, escalation, payoff]
    chooser = random.Random(seed)
    stagings = [chooser.choice(_OPENING_STAGINGS)] + chooser.sample(_STAGINGS, len(beats) - 1)
    prompts = []
    for staging, beat in zip(stagings, beats):
        # Either person can be the one nearer the lens.
        x, y = (_A, _B) if chooser.random() < 0.5 else (_B, _A)
        prompts.append(_SCENE.format(backdrop=backdrop, staging=staging.format(x=x, y=y), beat=beat.format(a=_A, b=_B)))
    return prompts
