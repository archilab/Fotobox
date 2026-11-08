# Architecture Decision Records

## ADR-001: Minimal Pi hardware
Phase 1 is one Raspberry Pi with standard UVC webcam only. No touchscreen/printer until later.

## ADR-002: Central Mac Studio service
FastAPI hosts API/metadata/matching; AI worker lives on Mac Studio via replaceable provider interface.

## ADR-003: Portrait pool
When no live counterpart exists, use authorized temporary portrait pool, then explicitly authorized seed images. Live matching introduced only with multi-box phase.

## ADR-004: Delivery output
Artifact is a vertically rendered PNG stored on server and retrievable by internal Pi client. This is NOT a public digital image retrieval product.

## ADR-005: MVP mock
The runnable vertical slice generates an unmistakably labelled synthetic mock composition. Model benchmarking and true two-person photographic composition postponed to AI milestone.

## ADR-006: Privacy separation
Consent for immediate strip and later pool use are separate purposes. CLI consent is external test procedure only; touchscreen UI comes later.

## ADR-007: Explicit operator attestation in the CLI
`capture` no longer sends `documented_externally: true` automatically. The operator must pass `--attest-documented-consent`, and without it the CLI stops before using the camera or network. This is still an operator assertion, not proof of consent.

## ADR-008: Development host and early AI test
Development runs on a MacBook Pro M3 (64GB) with the Pi on the same LAN; the server is ported to the Mac Studio M3 Ultra (256GB) before M6, and NetBird or Tailscale replaces the plain LAN later. Host-specific values stay in configuration. Because identity retention is the largest product risk, a standalone AI benchmark runs alongside M1, outside the application code, and its result is reviewed before M2. MacBook results show feasibility only; timings are measured again on the Mac Studio.

## ADR-009: Standard-library `.env` loading
The server reads `.env` with a small built-in parser instead of adding a dependency. Variables already set in the shell take precedence. Loading happens at server startup, not at import, so tests are not affected by a developer's local `.env`.

## ADR-010: Plain LAN with device tokens during development (accepted risk)
**Status: chosen by the project owner on 2026-11-07; reviewed the same day (see `docs/SECURITY.md`), and the required fixes are in ADR-013.** `CLAUDE.md` asks for authentication/TLS and a review before remote access. For development only, the server binds to the Mac's LAN address with device tokens but without encryption, so portraits and tokens can be read by others on that network. This is limited to a trusted LAN and to synthetic or expressly authorized images. It does not cover exhibition visitors or public networks. NetBird or Tailscale replaces it later and needs no code change, only a different server address.

## ADR-011: Deny-by-default device authentication
Authentication runs in middleware and covers every path except `health`, including `/docs`. A route dependency would be too late, because FastAPI parses multipart bodies before running dependencies, letting unauthenticated clients spool uploads to disk. Tokens are 256-bit random values stored as SHA-256 hashes; a slow password hash is not needed for values with this much entropy. `box_id` now comes from the authenticated device, not the request body. The `POST /admin/cleanup` endpoint was removed in favour of a local admin command, which leaves no admin surface on the network.

## ADR-012: Headless OpenCV on the client
The `client-camera` extra uses `opencv-python-headless`, because the CLI needs no GUI functions and the Pi client must not depend on a display.

## ADR-013: Strips expire with their portraits, and cleanup runs automatically
The ADR-010 review found that strips outlived retention even though each one contains portraits. A strip is now unavailable as soon as its own or its pool-matched portrait expires, and cleanup then deletes it. Seed images do not count, because they are not temporary. So a strip without pool consent lasts about one hour. The server runs cleanup at startup and every `PHOTOBOX_CLEANUP_INTERVAL_SECONDS` (default 300), which brings forward the basic part of M3's periodic deletion; revocation and queued jobs remain in M3. Cleanup also deletes files in `portraits` and `generated` that no record refers to, once they are an hour old, so requests still in progress are left alone. Storage folders are 0700 and stored files 0600, regardless of umask.

## ADR-014: FLUX.2 klein 4B through mflux as the first image model
**Go, decided by the project owner on 2026-11-07, who judged the quality sufficient** after two runs on fictional faces, including two similar-looking people (see `benchmarks/ai_feasibility/README.md`). FLUX.2 klein 4B is Apache 2.0 licensed, takes about 40–45 s per image and peaks at about 10 GB of MLX memory on the MacBook. The Qwen-Image-Edit-2511 run on faces and the ComfyUI comparison are dropped. The proposed numeric thresholds and `scores.csv` were not used, so this is the owner's overall judgement rather than a score. Known limits at this decision: similar faces drift toward each other, and real webcam photos were untested. M4's evaluation set later passed, including one real person (`docs/TEST_PLAN.md`); two real faces together are still untested. The model is reached only through the M4 provider adapter, never from route handlers, and `MOCK` stays until then.

## ADR-015: State changes through conditional updates, on one server process
M2 uses SQLite itself rather than a separate lock: each state change is a conditional `UPDATE`, which one concurrent request wins, and which holds the write lock until the file and record commit. Repeats are recognized by an `Idempotency-Key`, stored on the session or portrait rather than in a general table, because each request type has one natural owner. The upload limit lives in memory, so it resets on restart and only works with one process, which is acceptable for one trusted Pi; `docs/ARCHITECTURE.md` requires one process. Consent moved from two flags to one record per purpose, with a policy version and an optional outside reference, and existing data is migrated by versioned migrations (`docs/DATA_MODEL.md`). This still records an operator's statement, not proof of consent.

## ADR-016: Pool copies, admin-only seeds, operator-run withdrawal
Pool consent now creates a second file in `pool_portraits`, with its own lifetime, so a session image can expire within an hour while the pool copy lasts longer. Portraits stored in `portraits` as pool images before this change stay usable until they expire. Matching checks expiry and an unwithdrawn pool consent at the moment of use. Among eligible images, each is chosen with weight `1 / (1 + use_count)`, so less-used images come up more often without any being excluded, and pool images are preferred over seeds.

Seeds come only from `admin seed add`, which requires a rights attestation and reference, validates the image and removes its metadata. Files placed directly in the folder are ignored, which also means seeds from before this change must be imported again. Strips containing a removed or no longer registered seed are deleted.

Withdrawal is a local admin action with no HTTP endpoint, because phase 1 has no public retrieval or self-service. At consent, the operator receives a random code, once, to give to the participant; only its hash is stored. Withdrawal, by code or session id, marks consent withdrawn and deletes the session's images and every strip containing them, including other sessions' strips. A render in progress cannot complete afterwards. The audit trail records consent, use, deletion and seed events with opaque ids only. It has no retention limit yet, and that needs a decision before public use.

## ADR-017: Asynchronous jobs in SQLite, one worker thread, mflux as a subprocess
`POST /process` now only queues, for every provider, and clients poll the session; a model taking minutes per strip cannot hold a request open, and the API must not depend on the provider. Jobs live in `generation_jobs`, so no separate queue service is needed. One worker thread inside the server runs one job at a time, because a model uses most of the memory. The interface works per frame, so a retry or restart continues from finished frames. Attempts are bounded and also count interruptions, so a job that keeps crashing the server stops.

The FLUX.2 adapter runs mflux as a separate process in its own process group. This keeps its dependencies out of the project, and lets a timeout, cancellation or shutdown stop it completely. It runs with `HF_HUB_OFFLINE=1`, because generating from portraits must never involve the network, and its log is discarded because it can contain paths. A strip is only kept if the images it was started with are still valid at the end, and otherwise the job retries with a new counterpart.

The mock now requires `PHOTOBOX_DEVELOPER_MODE=true`. A failed job fails visibly and never falls back to the mock, so a mock can never be mistaken for AI output. Strips show their origin in the footer.

## ADR-018: Output profiles made on request, frames checked as images only
Only the colour strip is stored. Grayscale and thermal versions are made when requested, so there are no further copies of people's images to expire or withdraw. Frame checks cover resolution, readability and blank images. They deliberately do not compare the people across frames, because that would need face comparison, which the architecture rules out, so subject consistency is judged by eye. The layout is in pixels, and `PHOTOBOX_STRIP_WIDTH` and the thermal settings stay provisional until a printer is chosen: dithering should happen at the printer's real pixel width.

## ADR-019: Photo booth micro-stories, extra people allowed, face count optional
At the owner's request, strips look like a photo booth rather than four documentary scenes: one plain backdrop per strip, close 4:3 frames shot at eye level, even borders and a dated footer. Each strip tells one of 100 micro-stories (`photobox_server/stories.py`), chosen with its backdrop from the job's random seed, so a resumed job keeps both. All frames share a seed. The two participants are the main characters, and the opening frame keeps both faces clearly visible. Invented extra people are allowed, adults only. Stories avoid romance, unkindness about looks, danger and weapons, because matches can pair strangers; tests check for this.

A face count (`photobox_server/faces.py`, OpenCV 4 Haar cascade `alt2`) can limit invented people through `PHOTOBOX_MAX_FACES`, and is off by default since extras are welcome. It only counts faces and never identifies anyone. Its settings were chosen on 36 earlier frames, where it caught both frames with an invented third person, with one false alarm. In a later run it caught the one invented person and had no false alarms. It needs the `server-ai` extra, because OpenCV 5 no longer includes the cascade classifier.

The M4 evaluation scores (`docs/TEST_PLAN.md`) were for the earlier scenes and do not carry over to these prompts.

## ADR-020: Keyboard kiosk with on-screen consent (part of phase 2, brought forward)
**Decided by the project owner on 2026-11-07.** Until now the Pi's screen was only a console, and consent on the device was planned for phase 2 with a touchscreen. The owner asked for a keyboard flow on the existing screen instead, with no touchscreen. `photobox_client.kiosk` runs on tty1: ENTER shows the server's consent notice, a second ENTER confirms it, and only then is consent recorded and the photo taken after a countdown. ESC or 90 s without an answer cancels. While the strip is made, a timer and frame progress are shown, and at the end the withdrawal code.

Consent is recorded as `participant_screen`, with the notice's version: a hash of its exact text, served by the server from `consent-notice.txt`. A notice that still has `TODO-OPERATOR` placeholders is refused, and a changed notice invalidates confirmations of the old one. Keys pressed before a question appears are discarded, so a double press never counts as consent. The kiosk asks only for processing consent, never for pool use, because one ENTER should not cover two purposes.

Limits: the notice is the operator's draft, not legal review. A confirmation by key press records that the notice was confirmed, not who confirmed it, and checks no one's age. The withdrawal code is shown on screen, where others may see it. Phase 2's accessibility requirements, such as larger text and other languages, are not met yet.

The finished strip is shown on the screen for 20 seconds, with the withdrawal code beside it, by writing to the Linux framebuffer (`photobox_client/display.py`); no window system or extra package is needed. The owner asked for printing instead whenever a printer is connected. Printing is not implemented until the printer's specifications are known, so the strip is always shown for now. It is visible to anyone near the screen, as the code is.

## ADR-021: Setup menu on the box, pairing with a code
**Decided by the project owner on 2026-11-07:** pairing with a code, the server address typed in (no automatic discovery), a key combination for the menu, and a polkit rule for WiFi. The kiosk now reads its settings itself from `~/.photobox-kiosk.env`, which the setup menu (`photobox_client/setup_menu.py`) writes. The menu opens with M during the first 10 seconds after start, with Ctrl+Alt+S on the start screen, or by itself when network, server or pairing fail. It is protected by a PIN of 4 to 8 digits, set on first use and stored as a salted PBKDF2 hash; three wrong entries lock it for 30 s, doubling each time up to 15 minutes. Whoever opens the menu before a PIN exists sets it, so set one during installation.

The kiosk turns off the terminal's XON/XOFF flow control, so Ctrl+S reaches it instead of pausing the screen.

WiFi goes through NetworkManager's `nmcli`. A polkit rule (`deploy/pi/50-photobox-network.rules`) lets only the user `admin` perform four NetworkManager actions. The password reaches `nmcli` on standard input through its connection editor, never as an argument visible in the process list, and is never shown or logged. A failed connection removes the new profile, so the previous network returns.

Pairing: `admin device pair NAME` shows a 6-digit code, valid for 5 minutes and usable once. Only the newest code is active, five wrong guesses end it, and `POST /api/v1/pair` is limited to 10 attempts per minute per address. It is the only public endpoint besides health, and it rejects every request while no pairing is active. Pairing an existing device replaces its token. Only a hash of the code is stored, and the audit trail records the pairing without the code.

## ADR-022: Operator dashboard on the Mac only
**Decided by the project owner on 2026-11-08:** access from the Mac only, full story editing, strips shown directly, Jinja2 as a dependency, and status reports from the boxes. This is the first administrative web interface, so it keeps ADR-011's intent: `photobox_server.dashboard` is a separate app on `127.0.0.1:8001` (`scripts/run_dashboard.sh`), separate from the booth server on the LAN, sharing its database but never running jobs. It refuses any client that is not the Mac itself and any Host header other than `127.0.0.1`, `localhost` or `[::1]`, which also blocks DNS rebinding. It needs a password of at least 10 characters (`admin dashboard set-password`, PBKDF2), keeps logins in memory for 8 hours with a strict HttpOnly cookie, locks out after five wrong passwords, protects every form with a per-session token, and sends no-store, no-frame and content-security headers.

Content becomes versioned data. Stories, backdrops, stagings and the scene template are a prompt set whose version is a hash of its content; the original 100 stories are the default version. Every save passes the same rules the tests enforced, and is refused otherwise. A job records the version it was queued with and keeps it even if the content changes. Consent notices are likewise kept per version, so each consent stays linked to its exact text; until the dashboard saves one, `consent-notice.txt` is served as before.

Boxes report their state (idle, consent, capturing, generating, showing, setup or not ready) to `POST /api/v1/devices/status` on every change and every 30 s; a box is shown as offline after 90 s of silence. Reports carry no participant data.

Not included: making a test strip from the dashboard (use `scripts/evaluate_pairs.py`), and remote access to the dashboard, which waits for an encrypted network.

## ADR-023: Social presence from recorded events, shown truthfully
**Decided by the project owner on 2026-11-08**, adopting `docs/INTERACTION_EXTENSION.md` (V0.3) with these adaptations. The booth already has a screen (ADR-020), so presence is shown on the kiosk's idle screen as text, besides the dashboard preview of milestone S2. Updates are polled for now; S2 adds server-sent events rather than a WebSocket, which needs no new dependency. The spec's reference to an interaction extension V0.2 is void: no such document exists, and V0.3 is treated as complete.

Presence is derived, never guessed. The booth server writes typed events (`photobox_server/presence/events.py`) into a `presence_events` table inside the same transaction as the action they describe, so an event exists exactly when its action committed. A pure projection (`presence/projection.py`) turns those events and each box's latest status report into a snapshot. Keeping events in SQLite rather than in memory lets the dashboard, a separate process, read the same state, and a restart loses nothing. Events hold opaque ids, a type and times, never image data or anything about a person, and are deleted after an hour, so they never become a history of visits.

Rules:
- A box is **online** if it reported within 90 s. Online never means someone is there.
- A box is **active** (LIVE) from a session's recorded consent, not from its creation: before consent nothing about the visitor is signalled. It stays active while the box keeps reporting a session state (consent, capturing, generating, showing), and ends when the box reports idle, setup or not ready, when it goes offline, when the session is cancelled or withdrawn, or 5 minutes after the last sign of the session.
- A **recent photo** lasts 3 minutes after an upload (`PHOTOBOX_PRESENCE_RECENT_SECONDS`).
- An **earlier portrait** is available only while a visitor's pool portrait with valid pool consent exists; seed portraits never count. This is read from the portraits table on every snapshot, so withdrawal and expiry take effect at once.
- Repeated events are ignored by event id, and an event can never revive a session that has ended.
- Simulated events are marked and never appear in a box's snapshot.
- Detail defaults to **coarse** (`PHOTOBOX_PUBLIC_ACTIVITY_DETAIL`): no ages are published, only states.

Public names come from an operator-set label per box (`admin device label`, or the dashboard), never from device ids or addresses. Messages come from a catalog in German and English (`presence/messages.py`); the kiosk writes umlauts as plain letters, like its other screens. Labels are cleaned of control characters, so they cannot inject terminal escape codes, and are inserted in one pass, so a label cannot introduce further placeholders. A box without a label is called "another Photobox". The consent notice must say that taking a photo is signalled to other boxes before this goes beyond one box.

**Preview (milestone S2):** the dashboard's Presence page shows what a chosen box would be told, in German or English, in a 16:9, 9:16 or 4:3 frame. It includes simulated boxes, marked on each tile and with a SIMULATION banner, which a box's own snapshot never does. Updates arrive as server-sent events from the dashboard, which reads the shared database every 2 seconds and sends the snapshot when it changes; no WebSocket and no new dependency. The update script is a separate file, so the dashboard's content security policy still forbids inline scripts, and it sets text only, never HTML. The page works without it. Status is told by word and symbol, not only colour, and the LIVE pulse stops when the system asks for reduced motion.

**Simulator (milestone S3):** up to five virtual boxes, controlled from the Presence page, only in developer mode (`PHOTOBOX_DEVELOPER_MODE=true`); otherwise the controls are hidden and their routes answer 404. The spec's `POST /api/v1/dev/simulator/events` on the booth API is deliberately not built: that API faces the LAN with only device tokens, while the dashboard is limited to the Mac, needs a login and protects every form. Virtual boxes live in their own table (`simulated_boxes`) with ids like `sim:1`, which no real device can have, and each action causes the events a real box would, marked simulated: connect, disconnect, start, capture, finish, cancel, and expire, which moves the box's events back by the session lease. A box's own snapshot includes neither, and the simulator never touches sessions, portraits, consent or jobs. Starting a simulated session is a conditional update, so two clicks at once open one session. Tests run the spec's four scenarios against simulated boxes with the real box `pi-01`.

## ADR-024: Live matching across boxes, and timed pool consent at the kiosk
*The separate pool question below was replaced by one confirmation in ADR-030.*

**Decided by the project owner on 2026-11-08.** This brings part of phase 4 forward. When a visitor's photo arrives, the server first looks for a visitor at another box who has also just taken an unmatched photo, and pairs them live. If none arrives within 20 s, it matches with a visitor pool portrait, and only then with a seed. Both live visitors get the identical strip. Until a second Pi exists, the second box is a second kiosk on the Mac, using a test image or the webcam, plus the simulator; only consenting people may be photographed.

The kiosk will offer pool use as a second, separate question after the processing consent, never bundled with it: ENTER keeps the photo in the pool for 24 hours (`PHOTOBOX_POOL_HOURS`, set by the operator), ESC keeps it out, and the screen states the exact end time. It is recorded as its own `pool` consent with the notice version that described it, and expires or is withdrawn like any pool portrait. This replaces ADR-020's rule that the kiosk never asks for pool use, which was about not covering two purposes with one key press; separate questions satisfy it.

Implementation of P-1: the pool question is its own notice kind (`consent_notices.kind = 'pool'`, or `pool-notice.txt`), edited on the dashboard's Pool page. It must contain `{until}`; the server fills in the end time, rounded down to the minute, and its version includes the hours (`pool-<hash>-24h`). The box sends that end time back with the consent, the server checks it lies within the allowed hours, and stores it in `consent_records.expires_at`; the pool copy expires then at the latest. Without a usable pool notice the box does not ask, so pool use stays off until the operator writes one. The operator can remove one photo from the pool, which deletes the strips of others that used it, but not the visitor's own strip.

Implementation of L-1. **Decided by the project owner on 2026-11-08:** live pairing is covered by the consent notice rather than a third question, because a live pair is the strip being made now, with someone who is there now. It is still recorded as its own consent purpose, `live`, with the notice's version, and only when the operator has switched it on (`PHOTOBOX_LIVE_MATCHING=true`), which they should do only once the notice says so (`consent-notice.live.example.txt`).

How it works (`photobox_server/live.py`):
- `POST /process` takes the database's write lock first. It looks for the longest-waiting visitor at another box whose job is still waiting, has live consent and is unpaired.
- **If one is found**, the two are paired with conditional updates. The earlier visitor leads: their job starts at once with the newcomer's session photo as counterpart. The newcomer follows: it has no job of its own, reports the leader's progress, and on completion receives the same strip file.
- **If none is found** and another real box is online, the job waits up to 20 s (`PHOTOBOX_LIVE_WAIT_SECONDS`, 5-60). With no other box online it starts at once. After the wait the job picks a pool portrait or a seed as before.

When something goes wrong:
- **Before the strip is done:** if one side cancels or withdraws, the pair breaks. A departed follower's photo is removed from the leader's frames, and the leader starts again with another counterpart; a departed leader's follower is queued on its own. A failed strip fails for both and breaks the pair, so either can try again alone.
- **After the strip is done:** the pair stays recorded, so withdrawing or expiring either photo deletes the shared strip for both.

The box's status shows `match.kind` (live, pool or seed), the partner's public name and whether it is still waiting, and its start screen says "You're connected with …". `match.live_created` and `match.pool_created` are now emitted.

Implementation of L-2. **Decided by the project owner on 2026-11-08:** the 20 s wait stays the base, but if a visitor at another box has already given live consent, the waiting job keeps waiting for them, up to 60 s in total (`PHOTOBOX_LIVE_MAX_WAIT_SECONDS`, 20-120), because reading the notice and the countdown take about as long as the base wait. It only ever waits on a real, consented visitor. The kiosk shows the server's real time left, never an invented countdown, and says who is on the way. Other boxes' start screens say "Someone in Berlin is waiting for a shared photo" (`match_available`) while that is true, and refresh every 3 s. The moment two visitors are paired, both screens announce it for 3 s and then show their shared progress. If nobody came, the screen says so.

Milestones P-1 (timed pool consent), L-1 (live matching) and L-2 (the interactive waiting screen) follow S1 in `docs/MILESTONES.md`. Voice messages (milestones M-VOICE) stay limited to live pairs and come after L-1.

## ADR-025: Ephemeral messages between live pairs, contract first
Spec section 15, milestone M-VOICE-0. `photobox_server/messaging.py` holds the message relay and its rules; only the dashboard's developer-mode simulator uses it so far, and no real box can send or receive yet.

- A window opens only for a live match with visitors at two different boxes, never for a pool match or seed. It closes at the earliest of the strip being ready, failure, cancellation, a visitor leaving, or 45 s, and the server enforces that deadline.
- The caller names a match, never a destination. The server resolves recipients and derives the sender's name. An unknown match and someone else's match get the same refusal, so ids cannot be probed.
- Limits: 160 characters, 3 messages per sender, 3 s between messages, 8 s on screen (`PHOTOBOX_MESSAGE_*`). Text that is too long is refused, never cut. A repeated message id is delivered once. Control characters are removed; HTML stays plain text for the page to escape.
- Everything is in memory and bounded. Text is never written to the database or a log. An unread message disappears when it expires or its window closes, and nothing is replayed after a disconnect.

The relay runs in whichever process creates it, here the dashboard, so the simulator's messages exist only there. M-VOICE-2 will create the real one in the booth server, tie windows to real jobs, and add message filtering, which the spec requires before visitors can send. **Not implemented yet:** filtering, a box-facing API, microphone capture and speech-to-text. The notice must disclose messaging before any of it goes live.

## ADR-026: Microphone and speech-to-text on the Pi with whisper.cpp base
**Decided by the project owner on 2026-11-08**, after the benchmark in `benchmarks/voice_stt`. Milestone M-VOICE-1, in `photobox_client/speech.py`, behind the `LocalSpeechTranscriber` interface the spec asks for. On one speaker with a RØDE NT-USB Mini, whisper.cpp got every word right in German and English, while Vosk small got 18.8% of English words wrong. Against tiny, base is more accurate on synthetic speech for about one more second per message. It takes about 2.3 s with a fixed language and about 4 s with automatic language detection, which is the default because visitors may speak either language. The limit is 10 s.

- **Audio stays in memory.** `arecord` writes raw audio to a pipe, and whisper reads it on standard input. It is never written to disk or sent, and it is released after transcription.
- **Recording needs a deliberate key press and an open window.** `PushToTalk` records only while its `window_open` says so, and discards the audio or text if the window closes meanwhile. A key held down cannot stop the recording it started. Recording ends at 8 s.
- **Nothing invented may reach a stranger.** On silence and noise, whisper wrote text such as `* Musik *` and `[water flowing]`. A recording is passed to the model only if it holds at least 0.25 s of frames louder than both 150 and three times the room's own level. Annotations and known subtitle phrases such as "Thanks for watching" are then removed. The first threshold of 400 rejected a real short sentence and was lowered after measuring: room about 25, speech peaks about 1300.
- **It stays optional.** A missing microphone or a failed transcription only means no message. The microphone is chosen by card name in the setup menu, which records nothing.

Tested on the real microphone: full 8 s sentences, an early stop with ENTER, and silence. **Not tested:** noise, other voices, accents, children. The kiosk was connected afterwards, only for windows the server opens (ADR-027). `python -m photobox_client.speech test` is a developer tool that opens no window and sends nothing.

Installed on the Pi in `~/photobox-speech`: a statically linked `whisper-cli` from whisper.cpp v1.7.4 and `ggml-base.bin`.

## ADR-027: Messages between real live pairs
Milestone M-VOICE-2, connecting ADR-025's relay to real boxes and ADR-026's speech to the kiosk. **The project owner accepted the proposed filter on 2026-11-08 by continuing after it was proposed; it is easy to change.** Off by default: `PHOTOBOX_MESSAGING=true`, and only with live matching and a notice that says so (`consent-notice.live.example.txt`).

- **The relay lives in the booth server's memory.** A window opens when a live pair's strip starts (`message_windows.py`), for the two sessions the database says are paired, keeps its deadline across retries, and closes when the strip is done or has failed.
- **The database is checked on every send and poll**, because withdrawal can come from the dashboard or the command line, which are other processes. If the pair has ended or the other box has gone silent for 90 s, the window closes.
- **API:** REST, like the rest of the box's API: `GET /messaging/events` hands over a box's events once, and `POST /sessions/{id}/messages` sends one message. The kiosk polls every second while it is already showing progress, so no WebSocket was needed. A destination in the request is ignored.
- **Filter** (`message_filter.py`): a word list the operator edits on the dashboard's Messages page, in German and English, plus phone numbers, email addresses, links and social media names, so that strangers cannot swap contact details. The sender only sees "Message not sent". This is a first check and will not catch everything.
- **Kiosk** (`photobox_client/conversation.py`): ENTER counts only while the server has a window open. Keys pressed before the invitation appeared are discarded. A box without a microphone still receives text and shows no prompt. If the connection is lost, the window ends and nothing is queued or replayed. None of this delays the strip, and message text is transliterated for the console like the other screens.

From M-VOICE-3: the voice screens are in German and English like the rest of the kiosk, and the Messages page shows daily totals of pairs, delivered and blocked messages, kept for 30 days, with no text, session or box.

Tested over real HTTP with two boxes and an uninvolved third, with typed text in place of speech, and the microphone separately on the Pi (ADR-026). **Not tested:** two real kiosks with a microphone together, and noisy rooms; those belong to M-VOICE-3, along with the visitor-facing wording and an alternative for visitors who cannot speak.

## ADR-028: A browser screen for the box, with the text kiosk as fallback
**Decided by the project owner on 2026-11-08:** a minimalist black and white HTML/JS screen in Inter, always for a 1920 x 1080 display, German large and English smaller, with the setup menu kept as plain text at first.

The kiosk stays a Python program that makes every decision. The browser only draws. `photobox_client/views.py` describes each screen as a JSON-ready state; `ui/screens.js` draws one from such a state, setting all content as text and loading nothing from outside. Each state also carries `plain`, the same screen as console text, so the text kiosk keeps working unchanged and remains the fallback when no browser is available.

`photobox_client/webui.py` is the local server (U2), using only the standard library. It listens on `127.0.0.1` alone, refuses other Host headers, serves only a fixed list of files with a strict content security policy, and needs a token made fresh at every start for anything carrying content or accepting a key. The strip is held in memory only while it is on screen.

Consent keeps its rules in the browser. A key press names the screen it was pressed on, a press for an earlier screen is dropped, and a new screen discards waiting keys, so a double press cannot count as agreeing. A held key is one press. **A notice too long for one page cannot be agreed to until its last page has been on screen:** real notices needed two pages at a readable size, and ENTER on page 1 would have agreed to a page never shown. ENTER stays greyed out until then. On the 1080p display, notice text is 20 px where it fits and never below 16 px, after the owner found the first sizes twice too large on the real television.

The kiosk runs this with `--ui web`; the default is still `--ui text`. Milestones: U0 design prototype, U1 screen states, U2 local server and U3 on the Pi are done. U3 installed Chromium and Cage on the Pi, and U4 is the polish on the real television. Inter is included under the SIL Open Font License (`ui/fonts/OFL-LICENSE.txt`).

On the Pi, systemd runs `deploy/pi/run-kiosk.sh` in a login session on tty1. The kiosk starts `deploy/pi/start-browser.sh`, which runs Chromium under Cage, sets 1920 x 1080 with `wlr-randr`, and uses an incognito profile in memory. If the browser cannot start or keeps stopping (three restarts), or no browser opens the page within 60 s, the kiosk exits with code 75 and the script starts the text kiosk, tested by swapping in a broken browser. The token is passed to the browser as an argument, so it is visible in the Pi's process list to anyone logged in to the Pi; the only account is the operator's, and it only reaches this one local screen. It is no longer printed, so it does not reach the system log.

## ADR-029: Detecting a person at the box with the camera, on the Pi
**Decided by the project owner on 2026-11-08.** A box finds out whether someone is at it with its camera and a small AI model on the Pi, and tells the server and the other boxes in real time, with the box's public name. This reverses two rules of `docs/INTERACTION_EXTENSION.md` ("No automatic visitor detection" and "No surveillance-style detection") and the rule that nothing is analysed before consent. It necessarily runs before anyone has agreed, and can also catch people walking past. **That processing needs a legal basis, a data protection assessment and a sign before the box is used with the public;** this record is an engineering decision, not legal advice.

The owner chose a camera over an image-free presence sensor, knowing the sensor would be simpler legally.

Rules the implementation must keep, each to be enforced by a test:
- **Only on the Pi.** Frames stay in memory, are never written, logged or sent, and are released as soon as they have been analysed. The only thing that leaves the Pi is whether a person is there.
- **Only "a person is there".** No faces, identities, stored features, tracking between frames, counts, age or gender.
- **Small and bounded.** Low resolution, and only an operator-set area inside the box, to leave out passers-by as far as possible.
- **Visible.** A sign before the camera's view (`person-detection-sign.example.txt`) and a line on the start screen.
- **Off by default.** It runs only when the operator switches it on, and only while the start screen is showing. During a visit it is off; the camera is released before any photo.
- **Separate from consent.** The new state `person_nearby` never counts as LIVE, never starts a session and never takes a photo. LIVE still begins only with recorded consent (ADR-023).
- **Careful wording.** A detection can be wrong, so screens say "someone seems to be", and the state expires by itself if the box stops reporting.
- **No history.** It uses the existing presence events, deleted after an hour. A detector failure only means no signal; the booth carries on.

The detector is `photobox_client/person.py`: NanoDet from the OpenCV Zoo (Apache 2.0, chosen in `benchmarks/person_detect`), 3 frames per second at 640 x 360, smoothed so that a majority over about 2 s says yes and about 5 s without one says no. It is switched on with `PHOTOBOX_PERSON_DETECTION=true` in the box's settings, and limited to a picture area with `PHOTOBOX_PERSON_AREA`. The model file goes in `~/photobox-person/nanodet.onnx`; without it nothing runs.

A box reports only yes or no (`POST /devices/person`), stored as a `person.arrived` or `person.left` presence event. The state `person_nearby` ranks below LIVE and above a recent photo, ends when the box goes offline or after 30 minutes without an update, and creates no session and no consent. A kiosk says "nobody" when it starts, so a crash cannot leave a stale "someone". Other boxes get every change through `GET /presence/stream`, server-sent events, and fall back to asking every 3 s if the stream is down; the delay measured about 0.2 s.

Milestones D0 to D4 are in `docs/MILESTONES.md`.

## ADR-030: One confirmation for the photo and the pool
**Decided by the project owner on 2026-11-08, after being told it is legally weaker.** This reverses ADR-024's separate pool question and the rule that one key press never covers two purposes. The kiosk now shows the consent notice and, if a pool question is set, the pool text on one screen. ENTER agrees to both. There is no ESC: 45 seconds without ENTER cancels, with no photo and nothing recorded, and a countdown is shown. The kiosk replaces the operator's own key lines with this. With no pool notice set, ENTER means the photo only.

Pool consent is still its own consent record, with the pool notice's version and the end time shown, and can be withdrawn with the code like any other. **A visitor can no longer take a photo without joining the pool while a pool notice is active.** Consent that is bundled and a condition of the service is unlikely to count as freely given under the GDPR, so this needs legal review before public use. Removing the pool notice returns the booth to photos only. The notice must say that ENTER covers both, and must not say anything that contradicts it.

The screen after confirmation, decided by the owner the same day: from the consent's ENTER until the camera has finished, the browser screen is steady white as extra light, with a black countdown, and it goes dark as soon as the photo is taken. The withdrawal code no longer appears beside the strip. It has its own screen for 10 seconds with a countdown, and each ENTER adds another 10 seconds, up to a minute in all, because it is on a public screen. This replaces ADR-020's 20 seconds beside the strip and 60 seconds alone; a visitor who misses the code can no longer use it.

