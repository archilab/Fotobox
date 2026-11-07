"""Scene prompts for the two-person frames. Versioned, because each job records which it used."""
from __future__ import annotations

PROMPT_VERSION = "scenes-1"
_KEEP = "Keep each person's face, age and hair exactly as in their image."
SCENES: tuple[str, ...] = (
    f"A candid documentary photograph of the person from Image 1 and the person from Image 2 sitting together at a small cafe table, both facing the camera. {_KEEP} Natural light.",
    f"A candid documentary photograph of the person from Image 1 and the person from Image 2 walking side by side through a park in autumn, both looking toward the camera. {_KEEP}",
    f"A candid documentary photograph of the person from Image 1 and the person from Image 2 laughing while cooking in a home kitchen, both faces clearly visible. {_KEEP}",
    f"A candid documentary photograph of the person from Image 1 and the person from Image 2 seated next to each other on a train by the window, both facing the camera. {_KEEP}",
)
