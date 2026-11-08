# Operator guide

How to run the Fotobox day to day. To install it, see [INSTALL.md](../INSTALL.md). For an overview, see the [README](../README.md).

**Contents**
1. [What visitors see](#what-visitors-see)
2. [The dashboard](#the-dashboard)
3. [The setup menu at the booth](#the-setup-menu-at-the-booth)
4. [Deleting a visitor's pictures](#deleting-a-visitors-pictures)
5. [Changing the stories](#changing-the-stories)
6. [Where things are stored](#where-things-are-stored)
7. [Starting, stopping and restarting](#starting-stopping-and-restarting)
8. [Commands on the Mac](#commands-on-the-mac)
9. [Settings](#settings)
10. [Troubleshooting](#troubleshooting)
11. [Limits and responsibilities](#limits-and-responsibilities)

---

## What visitors see

| Screen | What the visitor does |
|---|---|
| **FOTOBOX – Press ENTER to start** | Presses ENTER. Above it, one line tells what is happening at the other booths, such as "Someone is taking a photo in Hall B", refreshed every 3 seconds (see *Booth names* below). |
| **Consent notice** | Reads it, then presses ENTER to agree or ESC to cancel. After 90 seconds without an answer, it cancels. |
| **Pool question** (only if you set one up) | A second, separate question: may the photo be combined with other visitors' photos until a stated time? ENTER = yes, ESC = no, and the photo is taken either way. No answer for 90 seconds cancels, with no photo. |
| **3 – 2 – 1** | Looks at the camera. |
| **Your photo strip is being made** | Waits about three minutes. A timer and "frame x of 4" show progress. |
| **The strip** | Sees the finished strip for 20 seconds, with their deletion code beside it. |
| **Done! Your withdrawal code** | Notes the code; it stays for 60 seconds or until ENTER. If the photo went into the pool, the screen says until when, and that the code deletes it there too. |

Keys pressed before a screen appears are ignored, so pressing ENTER twice quickly can never count as agreeing. No photo is taken before the visitor agrees.

**What happens behind the scenes:** the booth sends the photo to the Mac. The Mac picks a counterpart (one of your seed images), one of the stories and a background color at random, and creates four pictures, each with people at different distances and positions. Pictures that come out blank or broken are made again automatically. The photo and the strip are deleted after one hour.

---

## The dashboard

Open **<http://127.0.0.1:8001>** in a browser **on the Mac** and log in. It cannot be reached from other computers, on purpose: it shows faces and can change what the booth does.

| Page | What you can do |
|---|---|
| **Overview** | See whether each booth is **online** (it reported within the last 90 seconds) and what it's doing: waiting, consent, taking a photo, making a strip, showing it, setup or not ready. "Activity" shows what other booths are told: LIVE, RECENT, or NEARBY when its camera seems to see someone. Also how many strips are waiting, free disk space, and which consent notice and stories are active. Refreshes every 10 seconds. |
| **Queue** | Follow each strip frame by frame, see errors, and cancel a strip that is waiting or being made. The photo stays, so the booth can try again. |
| **Strips** | See all stored strips, in color, grayscale or a black-and-white printer look, and how many minutes until each is deleted. Delete a visitor's pictures here; see [below](#deleting-a-visitors-pictures). |
| **Consent notice** | Edit the text visitors agree to, with a preview of how it looks on the booth. |
| **Pool** | Edit the pool question (or leave it empty, and the booths never ask), and see the photos in the pool with the time each has left. No pictures are shown. "Remove from pool" takes one out, together with every other visitor's strip that shows that person. |
| **Stories** | Turn stories on or off, edit, add or delete them; see [below](#changing-the-stories). |
| **Prompts** | Advanced: the background colors, the text that describes every scene, and the list of positions people can take. |
| **Seeds** | Add or remove counterpart pictures. Tick the box confirming you may use the picture, and enter a short reference such as `FICTIONAL-AI` or a licence number. |
| **Presence** | See what a booth's start screen would say about the other booths right now, as a larger preview that updates by itself. Choose the booth, the language and the screen shape. Simulated booths, used for testing, are marked SIMULATION. In developer mode, a simulator below the preview adds up to five pretend booths with buttons for what a visitor does there; your real booths never see them. It can also try out text messages between two pretend booths. |
| **Messages** | Edit the words that visitors can't send each other, and see how many messages were delivered or blocked each day. Message text itself is never stored or shown here. |
| **Boxes** | Show a pairing code to link a booth, or switch a booth off for good ("Revoke"). |
| **Audit** | A log of what happened: agreements, strips, deletions, logins. It contains only codes, never pictures or names. |

Times in the dashboard are in **UTC**, which is one hour behind German winter time and two hours behind summer time.

**Forgotten the password?** Set a new one on the Mac:
```bash
cd ~/photobox-server && .venv/bin/python -m photobox_server.admin dashboard set-password
```

---

## The setup menu at the booth

Open it with **Ctrl+Alt+S** on the start screen, or by pressing **M** during the first 10 seconds after the booth restarts. It also opens by itself when the booth can't work, with the reason at the top. Enter your PIN.

The top of the menu shows a status line for **Network**, **Server**, **Pairing** and **Consent notice**: `OK` means fine, `--` means something needs attention.

| Choice | What it does |
|---|---|
| **1 WiFi** | Lists networks by signal strength. Pick one and type its password, which appears as `*`. If the connection fails, the booth goes back to the previous network. |
| **2 Server address** | The Mac's IP address, such as `192.168.1.24`. |
| **3 Pair with server** | Type the 6-digit code from the dashboard's **Boxes** page. |
| **4 Camera** | Choose the webcam and see a test picture for 5 seconds. It isn't stored. |
| **5 Test everything** | Checks all of the above. |
| **6 Start photo booth** | Back to the start screen. ESC does the same. |
| **7 Microphone (optional)** | Choose the microphone for later voice messages. Nothing is recorded here, and photos work without one. Used for voice messages, if you switch them on. |

Keys: a **number** to choose, **ENTER** to confirm, **ESC** to go back, **BACKSPACE** to delete.

**Forgotten PIN?** From the Mac, connect with `ssh admin@fotobox.local` and run:
```bash
rm ~/.photobox-setup.json
```
The next time the menu opens, it asks for a new PIN.

---

## Deleting a visitor's pictures

A visitor can ask you to delete their pictures at any time with the code they were shown.

**In the dashboard:** under **Strips**, find their strip, tick *Delete this visitor's images …* and click **Withdraw**.

**With their code:** on the Mac, run the following and type the code when asked:
```bash
cd ~/photobox-server && .venv/bin/python -m photobox_server.admin withdraw
```

Either way, the visitor's photo and **every strip they appear in** are deleted at once, and a strip still being made is stopped. Copies that someone saved elsewhere, and backups, can't be reached; that's why the storage folder is excluded from Time Machine.

---

## Changing the stories

Under **Stories** in the dashboard you see all stories. A strip uses one **enabled** story, chosen at random.

- **On / off:** click the button in the *Enabled* column.
- **Edit:** click a story's name. Each story has four parts: an opening where both faces must be visible, a development, an escalation and a payoff. Write `{a}` for the visitor and `{b}` for the counterpart. A strip with three pictures skips part 3, so parts 2 and 4 must make sense without it. Below the form you see the exact text sent to the AI.
- **New story:** click **New story**.

**What the booth refuses, and why:** stories must be suitable for strangers, because the visitor and the counterpart don't know each other. A story is refused, with the reason shown, if it contains romance (such as *kiss* or *hug*), weapons, alcohol, children, insults about looks, or camera directions like *seen from above* (the camera always stays at eye level). Every opening must name both `{a}` and `{b}`.

**Every change is kept as a version.** At the bottom of the page you can switch back to any earlier version, or to the original 100 stories. Strips that are already waiting keep the version they started with.

---

## Where things are stored

| What | Where (on the Mac) |
|---|---|
| Finished strips | `~/photobox-server/storage/generated/`, deleted after about an hour |
| Visitors' photos | `~/photobox-server/storage/portraits/`, deleted after an hour |
| Counterpart pictures | `~/photobox-server/storage/seed_portraits/` |
| Everything else (agreements, stories, log) | `~/photobox-server/storage/photobox.sqlite3` |
| Settings | `~/photobox-server/.env` |
| Logs | `~/Library/Logs/photobox-server.log` and `photobox-dashboard.log` |

Only your Mac user can open these files. To look at the strips folder:
```bash
open ~/photobox-server/storage/generated
```
Keep a strip only with the visitor's permission, and copy it before it's deleted.

On the booth (Pi), the settings are in `~/.photobox-kiosk.env`, the pairing in `~/.photobox.token` and the PIN in `~/.photobox-setup.json`. The booth keeps no photos.

---

## Starting, stopping and restarting

Everything starts by itself: the Mac's server and dashboard when you log in, and the booth when the Pi is switched on.

| Task | Command |
|---|---|
| Restart the server on the Mac | `launchctl kickstart -k gui/$(id -u)/com.photobox.server` |
| Restart the dashboard | `launchctl kickstart -k gui/$(id -u)/com.photobox.dashboard` |
| Stop the server until the next login | `launchctl bootout gui/$(id -u)/com.photobox.server` |
| Restart the booth | On the Pi: `sudo systemctl restart photobox-kiosk`, or switch it off and on |
| Show the Pi's login prompt instead of the booth | `sudo systemctl disable --now photobox-kiosk && sudo systemctl start getty@tty1` |

---

## The pool

Without a pool, every visitor is paired with one of your seed pictures. With a pool, visitors can agree that their photo is combined with later visitors' photos for 24 hours, so strangers meet in each other's strips.

**To switch it on**, write the pool question on the dashboard's **Pool** page, or copy `pool-notice.example.txt` to `pool-notice.txt` in `~/photobox-server` and complete it. The text must contain `{until}`, where the booth fills in the end time, and may contain `{hours}`. The booths ask from their next visitor on. **To switch it off**, press **Stop asking the pool question** on the Pool page, and delete `pool-notice.txt` if you created one. Photos already in the pool stay until their time; remove them one by one if needed.

Rules the server enforces:
- The question comes after the consent notice, never instead of it, and saying no changes nothing else.
- The photo leaves the pool at the time the visitor was shown, or earlier, never later.
- The withdrawal code deletes it from the pool too, and every strip it was used in.
- A visitor's photo is never paired with itself.

---

## Live encounters between booths

With two or more booths, visitors who take a photo at about the same time can share one strip. They are both in it and both get it. The first visitor's booth waits up to 20 seconds for someone at another booth, and shows "Looking for someone at another photo booth" with the time left. Meanwhile, the other booths' start screens say "Someone in … is waiting for a shared photo". If a visitor at another booth has already agreed to the notice, the wait extends until their photo arrives, up to 60 seconds, and the screen says someone is on the way. When the two are paired, both screens announce it and show the same progress. If nobody comes, the screen says so and the strip is made with a pool photo or a seed as usual. With only one booth online there is no waiting.

**To switch it on:**
1. Use a consent notice that says so; `consent-notice.live.example.txt` has the wording.
2. Set `PHOTOBOX_LIVE_MATCHING=true` in `.env`.
3. Restart the server.

**Trying it with one Pi:** the second booth can be a kiosk in a Terminal window on the Mac, with a test picture instead of a camera:
```bash
cd ~/photobox-server && .venv/bin/python -m photobox_client.kiosk --token-file ~/.photobox-mac-kiosk.token --config ~/.photobox-mac-kiosk.env --pin-file ~/.photobox-mac-kiosk-pin.json --test-image path/to/synthetic.jpg --no-display
```
Press **M** within 10 seconds, set a PIN, enter the server address, and pair it with a code from the dashboard's Boxes page under a new name such as `mac-kiosk`. The Mac manages its own network, so the WiFi part of the menu does not apply. Then press ENTER in that window as a visitor would. Use only synthetic pictures or people who agreed.

**Voice messages:** with `PHOTOBOX_MESSAGING=true` too, the two visitors can talk to each other while their strip is made: ENTER starts recording, ENTER stops it, for up to 8 seconds, and the words appear as text on the other booth. They can send 3 messages each, for at most 45 seconds. No sound is sent or saved, and text is never stored. It needs a microphone chosen in the setup menu and the speech software from `benchmarks/voice_stt/README.md`; a booth without them can still read messages. Blocked words are on the dashboard's Messages page. The filter won't catch everything, so have someone nearby when the public uses it.

If one of the two cancels or deletes their pictures before the strip is done, the other still gets a strip, with someone else. Afterwards, deleting either photo deletes the shared strip for both.

---

## Booth names

Other booths' start screens name the place where something is happening, such as "A photo was just taken in Hall B". Give each booth a public name in the dashboard under **Boxes** (or with `… admin device label pi-01 "Hall B"`). Use a place, never a person's name. A booth without a name is shown as "another Photobox".

What the start screen says is always based on what really happened:
- A booth that is only switched on is shown as **online**, never as someone being there.
- **LIVE** appears only after a visitor has agreed to the consent notice, and ends when that booth returns to its start screen.
- **JEMAND DA / NEARBY** appears only if you switched on person detection at that booth, and means its camera seems to see someone. It can be wrong and never counts as LIVE.
- **RECENT** lasts 3 minutes after a photo.
- "Someone left a photo for you" appears only while a visitor's shared pool photo exists, never for your seed images.

Your consent notice must tell visitors that other booths show when someone takes a photo here; the example notice has a line for it.

---

## Commands on the Mac

Most tasks are easier in the dashboard. Everything also works as a command, run in `~/photobox-server`:

| Task | Command |
|---|---|
| Delete a visitor's pictures by code | `.venv/bin/python -m photobox_server.admin withdraw` |
| … by session number | `.venv/bin/python -m photobox_server.admin withdraw --session SESSION_ID` |
| Show a pairing code | `.venv/bin/python -m photobox_server.admin device pair pi-01` |
| List or switch off booths | `… admin device list`, `… admin device revoke pi-01` |
| Set a booth's public name | `… admin device label pi-01 "Hall B"` (leave out the name to remove it) |
| Add, list or remove counterparts | `… admin seed add FILE --rights-reference REF --attest-rights`, `… admin seed list`, `… admin seed remove SEED_ID` |
| Show the log | `… admin audit --limit 50` |
| Delete expired pictures now (it also happens every 5 minutes) | `… admin cleanup` |
| Change the dashboard password | `… admin dashboard set-password` |

---

## Settings

Settings are in `~/photobox-server/.env`. After a change, restart the server. A setting with an impossible value stops the server, with a message naming it in `~/Library/Logs/photobox-server.log`.

| Setting | Default | Meaning |
|---|---|---|
| `PHOTOBOX_PROVIDER` | `mock` | `mflux-flux2` for the real AI. `mock` is a test stand-in that only works with developer mode. |
| `PHOTOBOX_DEVELOPER_MODE` | off | Keep off in use. |
| `PHOTOBOX_MFLUX_COMMAND` | `mflux-generate-flux2-edit` | Full path to the AI tool. |
| `PHOTOBOX_MFLUX_STEPS` / `_QUANTIZE` | `4` / `8` | AI quality settings. |
| `PHOTOBOX_NUM_FRAMES` | `4` | Pictures per strip: 3 or 4. |
| `PHOTOBOX_MAX_FACES` | not set | Optional: redo pictures that show more faces than this (2 to 20). |
| `PHOTOBOX_CONSENT_NOTICE_FILE` | `consent-notice.txt` | The notice used until you save one in the dashboard. |
| `PHOTOBOX_GENERATION_FRAME_TIMEOUT_SECONDS` | `600` | Longest time for one picture. |
| `PHOTOBOX_GENERATION_MAX_ATTEMPTS` | `3` | Tries per strip. |
| `PHOTOBOX_GENERATION_RETRY_DELAY_SECONDS` | `30` | Pause before trying again. |
| `PHOTOBOX_CLEANUP_INTERVAL_SECONDS` | `300` | How often expired pictures are deleted. |
| `PHOTOBOX_POOL_HOURS` | `24` | How long a photo stays in the pool after the visitor agrees (1 to 720). The pool question shows the exact end time, and the photo is deleted then at the latest. |
| `PHOTOBOX_LIVE_MATCHING` | off | `true` pairs visitors at different booths live. Only with a notice that says so. |
| `PHOTOBOX_MESSAGING` | off | `true` lets live pairs send each other short voice messages as text. Only with a notice that says so. |
| `PHOTOBOX_LIVE_WAIT_SECONDS` | `20` | How long a booth waits for someone at another booth (5 to 60). |
| `PHOTOBOX_LIVE_MAX_WAIT_SECONDS` | `60` | The longest wait while a visitor at another booth has already agreed (20 to 120). |
| `PHOTOBOX_POOL_NOTICE_FILE` | `pool-notice.txt` | The pool question used until you save one in the dashboard. Without one, the booths do not ask. |
| `PHOTOBOX_PRESENCE_RECENT_SECONDS` | `180` | How long a photo counts as "just taken" on other booths (30 to 1800). |
| `PHOTOBOX_PUBLIC_ACTIVITY_DETAIL` | `coarse` | `coarse` shows no times; `detailed` adds how long ago. |
| `PHOTOBOX_STORAGE_DIR` | `./storage` | Where photos, strips and the database are kept. |
| `PHOTOBOX_MAX_UPLOAD_BYTES` | `10000000` | Largest photo accepted. |
| `PHOTOBOX_MAX_UPLOADS_PER_MINUTE` | `30` | Per booth. |
| `PHOTOBOX_STRIP_WIDTH` | `560` | Strip width in pixels, for a printer later. |
| `PHOTOBOX_THERMAL_BRIGHTNESS` / `_CONTRAST` | `1.1` / `1.3` | The black-and-white printer look. |
| `PHOTOBOX_INTERFACE` / `PHOTOBOX_PORT` | `en0` / `8000` | The Mac's network connection and port. |

---

## Troubleshooting

### At the booth
| What you see | Cause and fix |
|---|---|
| **The text suddenly looks like an old computer** | The browser screen could not start, so the booth switched to its simple text screen by itself. Visits still work. Restart the booth; if it stays that way, see `sudo journalctl -b \| grep -i browser` on the Pi. |
| **The screen looks empty** | The text may be tiny on a large screen, or in a corner the TV cuts off. Restart the booth. Check the screen's input. |
| **The setup menu opens by itself** | The reason is at the top. Usually the Mac is off or asleep, its address changed, or the booth isn't paired. |
| **"Not ready"** | The Mac's server isn't running, or the consent notice is missing or still has `TODO-OPERATOR`. |
| **"Could not reach the server"** | The network dropped, or there's no counterpart picture: add one under **Seeds**. |
| **Only the code appears, not the strip** | The booth couldn't show the picture. The strip is still on the Mac. |
| **Ctrl+Alt+S does nothing** | It only works on the start screen. Restart the booth and press M within 10 seconds. |
| **WiFi: "could not save the network"** | The WiFi permission is missing: repeat INSTALL step 2.5. |
| **Camera errors** | Unplug and replug the webcam, then choose it again in **4 Camera**. |

### On the Mac
| What you see | Cause and fix |
|---|---|
| **The booth worked, but after a while it can't connect** | The Mac got a new IP address. Enter the new one in the booth's setup menu, or give the Mac a fixed address in your router. |
| **Dashboard: "only available on this Mac"** | Open exactly `http://127.0.0.1:8001` on the Mac itself. |
| **Dashboard says no password is set** | Run `admin dashboard set-password`. |
| **A booth shows "offline"** | It hasn't reported for 90 seconds: it's off, has no network, or isn't paired. |
| **`Operation not permitted` in a log** | The photo booth folder is inside Documents. It must be in `~/photobox-server`. |
| **`Bootstrap failed: 5`** | That service is already running. Nothing to do. |
| **Strips fail at once** | The AI model isn't downloaded yet; see INSTALL step 1.5. |
| **"mflux was not found"** | Set `PHOTOBOX_MFLUX_COMMAND` in `.env` to the full path, such as `/Users/yourname/.local/bin/mflux-generate-flux2-edit`. |
| **A strip has disappeared** | Strips are deleted with the visitor's photo after about an hour. |
| **`python: command not found`** | Use `python3`, or `.venv/bin/python` inside `~/photobox-server`. |

---

## Limits and responsibilities

- **Network:** photos travel between the booth and the Mac **without encryption**. Use only a network where you trust everyone, until an encrypted connection such as Tailscale or NetBird is added.
- **Consent:** pressing ENTER shows that the notice was confirmed, not who confirmed it or their age. The notice is your text and your responsibility. Public use in the EU needs a data protection assessment (GDPR).
- **Deletion codes** appear on the screen, where people nearby may see them.
- **Recognizability:** an earlier test scored 4.6 out of 5 with invented faces and one real person, but that was before the current stories, and two real people together haven't been tested.
- **Backups** can't be cleaned by the booth, so keep the storage folder out of Time Machine.
- **Not yet available:** printing, a touchscreen, several booths at once, and deletion by visitors themselves.

For the technical background, see the [technical documentation](README.md).
