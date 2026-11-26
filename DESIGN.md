---
name: MEET A STRANGER
description: The booth screen of MIDAP's photo booth, black ground, white Inter, and one continuous line per place.
colors:
  ground-black: "#000000"
  signal-white: "#ffffff"
  other-place-grey: "#9a9aa0"
  track-graphite: "#2e2e33"
  light-screen-grey: "#555555"
typography:
  numeral:
    fontFamily: "Inter, sans-serif"
    fontSize: "660px"
    fontWeight: 800
    lineHeight: 0.9
    letterSpacing: "-0.04em"
    fontFeature: "\"tnum\""
  display:
    fontFamily: "Inter, sans-serif"
    fontSize: "128px"
    fontWeight: 750
    lineHeight: 1.04
    letterSpacing: "-0.028em"
  code:
    fontFamily: "Inter, sans-serif"
    fontSize: "112px"
    fontWeight: 700
    lineHeight: 1.12
    letterSpacing: "0.02em"
    fontFeature: "\"tnum\""
  headline:
    fontFamily: "Inter, sans-serif"
    fontSize: "64px"
    fontWeight: 600
    lineHeight: 1.2
  title:
    fontFamily: "Inter, sans-serif"
    fontSize: "54px"
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  button-word:
    fontFamily: "Inter, sans-serif"
    fontSize: "48px"
    fontWeight: 700
  body:
    fontFamily: "Inter, sans-serif"
    fontSize: "40px"
    fontWeight: 400
    lineHeight: 1.3
  note:
    fontFamily: "Inter, sans-serif"
    fontSize: "38px"
    fontWeight: 400
    lineHeight: 1.28
  label:
    fontFamily: "Inter, sans-serif"
    fontSize: "36px"
    fontWeight: 600
    letterSpacing: "0.22em"
rounded:
  hairline: "4px"
  bar: "8px"
  full: "50%"
spacing:
  header-inset: "56px"
  word-gap: "26px"
  gutter: "72px"
  margin: "112px"
  button-reserve: "300px"
components:
  round-button:
    backgroundColor: "{colors.signal-white}"
    textColor: "{colors.signal-white}"
    typography: "{typography.button-word}"
    rounded: "{rounded.full}"
    size: "150px"
  round-button-track:
    backgroundColor: "{colors.track-graphite}"
    rounded: "{rounded.full}"
    size: "150px"
  header-bar:
    textColor: "{colors.other-place-grey}"
    typography: "{typography.label}"
  place-line-ours:
    textColor: "{colors.signal-white}"
  place-line-theirs:
    textColor: "{colors.other-place-grey}"
  withdrawal-code:
    textColor: "{colors.signal-white}"
    typography: "{typography.code}"
  meter:
    backgroundColor: "{colors.track-graphite}"
    rounded: "{rounded.bar}"
    height: "16px"
    width: "900px"
  light-screen:
    backgroundColor: "{colors.signal-white}"
    textColor: "{colors.ground-black}"
---

# Design System: MEET A STRANGER

All sizes in this file are canvas pixels. The screen is laid out on a 2560 x 1600 canvas and drawn at exactly half size on the booth's 7-inch 1280 x 800 panel (ADR-038), so every value here is twice what the panel shows. Any other display gets the same canvas scaled to fit, with bars.

## Overview

**Creative North Star: "Two Lines Meeting"**

The booth screen is a black field on which places are drawn, not described. Each booth owns one continuous white or grey line built from copies of MIDAP's own mark, and every encounter the booth makes is told as two lines approaching, crossing and knotting. Type is Inter, large and few: one message per screen in German, with at most one short English line where a visitor needs it to act (ADR-040).

The density is that of a sign read from a metre away, not of an interface. Most of the canvas is empty black; one hero sentence sits top left, the drawn line runs through the middle, and the round button sits bottom left on every screen where a press is possible. The only departure from black is the countdown and flash, where the whole screen turns white because it is the light for the photo.

The screen refuses the photo-booth default of a camera frame, stickers and a big "take a photo" button. It has no colour, no glow, no fills on the line, no icons and no imagery other than the visitor's finished strip.

**Key Characteristics:**
- Black ground, white type, one grey for the other place and for secondary text.
- Inter only, from a heavy 128 px hero down to a 36 px floor (18 px on the panel).
- One continuous stroke per place, MIDAP's mark repeated, one weight, round caps.
- The physical round button drawn as a circle in a fixed place, bottom left.
- Motion that draws and counts: lines drawn in, a pulse asking for a press, a ring emptying as time runs out.

## Colors

A strict black-and-white system with one cool grey that means "elsewhere" or "secondary" and one dark graphite for tracks.

### Primary
- **Signal White** (#ffffff): all primary text, this booth's line, the button's cap and its emptying ring, the filled part of a meter, and the entire screen during countdown and flash.

### Neutral
- **Ground Black** (#000000): the page and every screen's background; also the colour of the short gaps cut into the other place's line where ours passes over it.
- **Other-Place Grey** (#9a9aa0): the other booth's line and its place-name label, the header bar, English secondary lines, notes and info text, the list dash in the consent notice.
- **Track Graphite** (#2e2e33): the button's track ring, the empty part of a meter, the rule beside the encounter panel. Never used for text.
- **Light-Screen Grey** (#555555): English secondary text on the white light screen only.

### Named Rules
**The Here-and-Elsewhere Rule.** White is this booth, grey is the other place. A line, label or mark belonging to another booth is always grey; this booth's is always white. No third hue is introduced to distinguish places; places are told apart by their name and their line's shape.

**The Word-Not-Colour Rule.** State is said in words, by symbol, or by the line and the ring, never by colour. There is no red, green or accent anywhere on the screen.

## Typography

**Display Font:** Inter (variable, 100 to 900, bundled locally under OFL; fallback sans-serif)
**Body Font:** Inter
**Label Font:** Inter

**Character:** One family carries everything, pushed to the heavy end (700 to 850) for anything read at a glance and kept at 400 to 500 for the rare second line. Stylistic sets `ss02`, `cv05` and `zero` are on throughout; all numbers that count or must be copied use tabular figures.

### Hierarchy
- **Numeral** (800, 660 px countdown; 400 px invitation and listening seconds; line-height 0.9, -0.04em, tabular): a single number that fills the screen. The LIVE moment uses the same treatment at 330 px, weight 850.
- **Display** (750, 128 px, line-height 1.04, -0.028em, balanced wrapping): the one hero sentence per screen.
- **Code** (700, 112 px, line-height 1.12, 0.02em, word-spacing 0.3em, tabular): the withdrawal code, in groups of four, the largest thing on the code screen. "Fertig!" above it is 110 px, weight 800.
- **Headline** (600, 64 px, tabular): the time remaining in words while the strip is made.
- **Title** (500, 54 px, line-height 1.2): a lead line; the encounter panel's heading uses 54 px at weight 700. English under a hero is 58 px, weight 500.
- **Button word** (700, 48 px): the one word beside the round button; its English line is 36 px, weight 500, grey.
- **Body** (400, 40 px, line-height 1.3, two columns 72 px apart): the consent notice, fitted between 40 px and the floor; its title 44 px and its section heads 42 px, both weight 750.
- **Note** (400, 38 px, line-height 1.28, grey): info lines under the code, notes in the encounter panel, the camera note (500 weight, white German with grey English).
- **Label** (600, 36 px, 0.22em tracking, uppercase in content): the header bar only. The place names on the lines use 38 px, weight 650, 0.12em, uppercase, grey.

### Named Rules
**The Arm's-Length Floor Rule.** Nothing a visitor reads is smaller than 36 px on the canvas, 18 px on the panel (ADR-038). Operator-only text is the only exception.

**The One-Message Rule.** Each screen carries one German message and at most one short English line, and only where the visitor needs it to act (ADR-040). A second sentence, caption or label that repeats what the line, the light or the button already says is removed, not made smaller.

## Layout

A fixed 2560 x 1600 canvas, scaled as a whole; there are no breakpoints and no reflow. Every screen is a full-canvas column with a 112 px margin on all sides and 300 px reserved at the bottom for the button slot.

- **Fixed anchors.** The round button sits bottom left at the margin on every screen that takes a press. On the start screen MIDAP's signature and the camera note sit bottom right at the margin. The header bar ("MEET A STRANGER" left, the booth's place right) sits 56 px from the top, on the start screen only (ADR-040); start and invitation screens start their content at 196 px to clear it.
- **Hero top left.** The hero sentence is set flush left at the top margin; centred layouts are kept for screens that are a single number or word (countdown, listening, converting, plain messages).
- **The line band.** The drawing runs across the middle of the canvas, 2336 px wide (the canvas inside its margins); our line enters from the left, each other place's from the right, each in its own lane.
- **Two-part screens.** While the strip is made with a partner, a 1200 px encounter panel sits right of the main column with an 88 px gap and a 2 px graphite rule on its left; alone, the main column takes the full width. The strip screen sets the portrait strip at full height beside the code with a 100 px gap.
- **Rhythm.** 72 px is the recurring gutter (notice columns, action gaps, panel inset); 26 px separates a word from its companion line; vertical steps between blocks run 28 to 56 px.

## Elevation & Depth

Flat. There are no shadows, blurs, gradients or layered surfaces. Depth exists in exactly one place: where our line crosses the other place's, a 22 px stroke in Ground Black is laid under ours across theirs, so ours reads as passing over. Those gaps are placed only at crossings found on the drawn paths, never decoratively.

### Named Rules
**The Over-Under Rule.** The only depth on the screen is a line passing over another at a real crossing. Nothing is lifted, shadowed or glowed.

## Shapes

The form language is the stroke and the circle. Lines are 7 px strokes with round caps and joins, never filled. The button is a pair of circles. Rectangles appear only as the strip image (4 px corners, white backing) and the 16 px meter bars (8 px radius, fully rounded ends). Every curve in a place's line is either one of the five segments of MIDAP's mark or a smooth join continuing both strokes' directions; computed loops and hairpins are not part of the language.

## Components

### Round Button
The physical push button, drawn as it is. Always bottom left, always with one word beside it.
- **Shape:** a 150 px circle: a graphite track ring (r 63 of 140, 8 px stroke) around a white cap (r 44 of 140).
- **Label:** one German word at 48 px weight 700, 32 px to its right, with an optional English line at 36 px weight 500 grey.
- **Pulse:** while a press is expected, the cap breathes over 1.6 s (to 55% opacity and 90% scale and back), in step with the button's green light; off with reduced motion.
- **Ring:** where time runs out, a white 8 px ring with round caps starts at twelve o'clock and empties linearly over the remaining seconds. It replaces any "Noch 9 s" text and runs even with reduced motion, because it is information.
- **Locked:** on a consent notice not yet read to its last page, the button and word sit at 30% opacity until the last page has been shown.

### Place Line (signature)
One continuous line per booth, the booth's living element (ADR-039).
- **Construction:** copies of MIDAP's mark, mirrored to the direction of travel, varied per place only in size, slant and height, seeded by the place's name so a place always draws the same line.
- **Colour:** white for this booth, grey for another place, 7 px, round caps and joins, no fill.
- **Label:** the other place's name, 38 px uppercase grey, right-aligned above a straight lead-in at the start of its line.
- **Motion:** drawn in when the screen appears (2 s default, ease `cubic-bezier(.16, 1, .3, 1)`), continuing from where it was when the same screen is redrawn; shown complete with reduced motion. On the invitation the other line reaches across linearly while the seconds run; while the strip is made the lines advance one crossing (with a partner) or one loop (alone) per finished picture.
- **Where:** start, invitation, strip being made, LIVE moment, code screen. Never on consent, countdown or flash.

### Header Bar
"MEET A STRANGER" left and the booth's place right, 36 px weight 600, 0.22em tracking, grey, 56 px from the top. Start screen only; the name is left out when the headline is already "MEET A STRANGER".

### MIDAP Signature
MIDAP's mark (72 x 62 px, white stroke, no fill) with "MIDAP" at 38 px weight 750, 0.2em tracking, bottom right on the start screen only, with the camera note above it when the camera looks and the concept credit, "Konzept: Aida Salmanipour und Calvin Leonard Kröger", below it in grey at 36 px (ADR-042). The funding notice belongs beside it once its wording is given; nothing stands in its place until then.

### Resting Screen
After the booth's idle minutes without a press (ADR-041), essentials only. Left: the QR code for the project's website, 720 px square, white modules straight on the black screen (inverted, ADR-041), the four-module quiet zone left black; "midap.space" centred below at 64 px weight 750. Right, 200 px away: the footer of midap.space as three groups, each a grey 36 px weight 600 label ("Forschungsprojekt", "Gefördert durch", "Verbundpartner") over white logos (MIDAP 860 px wide, the foundation 620 px, Folkwang 470 px, UdK 720 px), 96 px between groups. No button, no sentence, no motion: the light on the real button asks for the press. Without a code, the address stands alone in its place at 96 px.

### Consent Notice
The operator's full text, unchanged: a 44 px title, then two columns 72 px apart at 40 px with line-height 1.3, uppercase lines promoted to 42 px heads, list items marked with a grey en dash. When it does not fit, it pages sideways with a tabular "Seite 1 von 2" counter that nudges upward when a press comes too early.

### Withdrawal Code
The code in groups of four at the code size, white, tabular, the largest element of the code screen, with one 38 px grey line below saying what it is for.

### Light Screen
Countdown and flash turn the whole screen Signal White with black type and no animation, because the screen is the light on the visitor's face. The countdown number is the only content.

### Meter
A 900 x 16 px graphite bar with an 8 px radius, filled white from the left in 1 s linear steps; used for the listening limit.

## Do's and Don'ts

### Do:
- **Do** set every size on the 2560 x 1600 canvas and keep visitor text at 36 px or more (18 px on the panel).
- **Do** draw the other place as a grey line labelled with its name, and this booth as a white line; let the line be the progress wherever something is being made.
- **Do** put the round button bottom left at the 112 px margin, with one word beside it, on every screen that takes a press.
- **Do** say time running out with the emptying ring, and time remaining in words ("Noch etwa eine Minute."), never with an elapsed clock.
- **Do** turn the screen fully white, without a fade, while the camera needs light.
- **Do** use tabular figures for every number that counts or must be copied.

### Don't:
- **Don't** add colour, glow, fills, gradients or shadows to anything the screen draws; the visitor's finished strip is the only image and the only colour, and the over-under gap at a real crossing is the only depth.
- **Don't** draw a line from computed loops or any curve that is not MIDAP's mark or a smooth join between copies of it.
- **Don't** show a camera frame, stickers or a "take a photo" button.
- **Don't** add a second sentence, a caption or a label that repeats what the line, light or button already says.
- **Don't** show the header bar, MIDAP's mark or a line on consent, countdown or flash screens.
- **Don't** invent the funding notice or a placeholder for it.
