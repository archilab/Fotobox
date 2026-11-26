---
version: 1
slug: "src-photobox-client-ui"
primary_target: "src/photobox_client/ui"
related_targets: []
---

# Booth screen (src/photobox_client/ui)

Scope: the booth's browser screen on the 7-inch panel, 1280 x 800. Start and invitation screens persuade passers-by; the visit screens are operated with one button. Audience: exhibition visitors, in passing, often in groups (PRODUCT.md).

## Direction contract

THESIS: The other place is drawn, not described: each booth owns one continuous line, in the stroke of MIDAP's mark, and the encounter is two lines meeting. Refuses the photo-booth default of a camera frame, stickers and a big "take a photo" button.

OWN-WORLD: Black ground, white type in Inter (ADR-028). Our line is white, the other place's line grey with its name beside it; one stroke weight, round caps, no fills, no colour, no glow. MIDAP's mark small on the start screen.

STORY: A passer-by sees a line drawing itself and, when another booth is live, a second line labelled with that place; at an invitation the other line reaches across while seconds count down; they press, agree, and watch the two lines weave one crossing per finished picture; the code screen shows them knotted.

FIRST VIEWPORT: Start screen: the header bar with the booth's place, one hero sentence top left, a band of line across the lower middle, our line from the left, each other place's from the right with its name above a straight start, the round button drawn as a circle with "Start" bottom left, MIDAP's mark and "MIDAP" bottom right. Cleanup agreed on 2026-11-25 (ADR-040): one message per screen, a short English line only where needed to act, header on the start screen only, the round button as a circle on every screen with a pulse and an emptying ring instead of countdown text, time remaining instead of a clock.

FORM: Extension of the established world; seed: none (pinned) "MIDAP's continuous line becomes the booth's living element. One line per place." and, for the first viewport, the owner's confirmed brief: "Start screen, nobody elsewhere: our line slowly draws a loop and comes to rest. Another booth is online or busy: a second line rests at the edge, labelled with the place."

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Open

- Funding notice wording and logo: not given yet; show nothing in its place.
- Camera preview, layered notice, touch, printed strip: out of scope.
