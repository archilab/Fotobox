# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Exhibition visitors: members of the public at exhibitions, open days and festivals of UdK Berlin and Folkwang. They pass by, often in groups, with little time and no intention to read. They meet the booth once, decide within seconds whether to step in, and use it standing, with one push button or the keyboard.

Operators (project staff) set the booth up, write the consent notice and stories, and watch it on the dashboard on the Mac. They are not the audience of the booth's screen.

## Product Purpose

"MEET A STRANGER" is a photo booth of the research project MIDAP (Multisensory in Dialogue and Artistic Practice), a cooperation of UdK Berlin and Folkwang. A visitor's photo becomes a short AI photo strip in which they meet another person in a small story. When someone takes a photo at another booth at about the same time, the two become one strip and both receive it, printed.

Success: a passer-by understands at a glance that the booth connects them with a stranger somewhere else, steps in, agrees knowingly, and leaves with a strip that shows that encounter.

## Positioning

A dialogue between places: strangers at two sites meet through one shared picture. The booth makes distance and the other place present (the other booth's name, live invitations, a shared strip), not the technology. No other photo booth pairs two real visitors in different cities in one strip.

## Operating Context

- Booths in exhibition spaces at different sites, connected over a network to one server on a Mac Studio that makes the strips with FLUX.2 (about 80 s per strip).
- Each booth: a Raspberry Pi 5 with a 7-inch touch panel at 1280 x 800, a USB webcam, a push button with RGB status lights, a receipt printer for 80 mm paper, and optionally a microphone.
- A visit: start, read and agree with one press, countdown with the screen lighting the face, photo, a strip made while the visitor waits, the strip printed and cut, then a withdrawal code on the screen for a short time.
- German first, English second, on every screen.

## Capabilities and Constraints

- Consent comes before any photo, as one press on a screen that says what it covers (ADR-030); nothing about a visitor is announced to other booths before they agree (ADR-023).
- Photos are deleted after an hour, or after 24 hours in the optional pool; each visitor gets a withdrawal code. A printout cannot be taken back.
- No digital retrieval of strips, no public gallery, no face recognition. Person detection at the booth, when switched on, only says whether someone is there (ADR-029).
- Live encounters pair two people; other booths are invited for 20 s from the moment someone agrees (ADR-037).
- Everything runs on the local network and the booth's own browser screen; nothing is loaded from outside.
- Open decision: whether a tap on the touch panel counts like the button (the project's phase rules say no touchscreen yet).

## Brand Commitments

- The booth's name on screen is "MEET A STRANGER".
- MIDAP's logo, the continuous-line mark shown on midap.space, may be used; the file is to come from the project.
- A funding notice is required. On the resting screen it is "Gefördert durch" with the logo of the Stiftung Innovation in der Hochschullehre, as in the footer of midap.space (ADR-041); elsewhere its wording is still to come from the project; do not invent it.
- The visual style chosen so far (ADR-028): minimalist, black background, white type, Inter.

## Evidence on Hand

- MIDAP's website, https://www.midap.space: full name "Multisensory in Dialogue and Artistic Practice", partners UdK Berlin and Folkwang.
- The repository's screens, the stories (`src/photobox_server/prompts.py`), and drawn test images. Counterpart faces are AI-made and rights-cleared.
- No photos of real visitors may be used as examples, and no testimonials, visitor numbers or research findings exist yet.

## Product Principles

1. The other place is the hero: every screen that can should show where the stranger is.
2. Understood in a glance from a step away; reading is optional, agreeing is not.
3. Honest about data: what happens to the photo is said plainly, before and after.
4. One action at a time: one button, one decision per screen.
5. The encounter is felt beyond the screen: light, print and the shared strip carry it too.

## Accessibility & Inclusion

Readable from about a metre on a 7-inch panel (text at least 18 px on the panel), German and English, usable with one button, motion reduced when the system asks for it. Not tested yet with visitors who need assistive technology.
