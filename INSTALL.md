# Installing the Fotobox

This guide takes you from an empty Raspberry Pi and a Mac to a working photo booth. You don't need to know how to program. You will copy commands into a window and press Return; each step says what the command does and what you should see afterwards.

**Time:** about one hour, plus the download of the AI model (about 15 GB).

**Contents**
1. [Before you start](#before-you-start)
2. [Part 1: The Mac](#part-1-the-mac)
3. [Part 2: The Raspberry Pi](#part-2-the-raspberry-pi)
4. [Part 3: Set up the booth with its keyboard](#part-3-set-up-the-booth-with-its-keyboard)
5. [Part 4: Your first photo](#part-4-your-first-photo)
6. [If something goes wrong](#if-something-goes-wrong)
7. [Updating later](#updating-later)

---

## Before you start

### Checklist
- [ ] A Mac with Apple Silicon (M1 or newer) and about 40 GB of free space
- [ ] A Raspberry Pi 5 with power supply and a memory card (16 GB or more)
- [ ] A USB webcam, an HDMI screen and a USB keyboard for the Pi
- [ ] Both devices in the same network, such as your home WiFi
- [ ] Your network's WiFi password

### A few words you'll meet
| Word | Meaning |
|---|---|
| **Terminal** | A Mac app where you type commands. Find it with Spotlight (⌘ Space, then type *Terminal*). |
| **Command** | A line of text you paste into the Terminal and run with Return. In this guide, each command is in its own grey box. |
| **SSH** | A way to type commands on the Pi from your Mac's Terminal. |
| **IP address** | A device's number in your network, such as `192.168.1.24`. |
| **`~`** | Short for your home folder, such as `/Users/yourname`. |

### How to run a command
1. Copy the whole grey box. On GitHub there's a copy button in its top right corner.
2. Click into the Terminal window and paste it with ⌘V.
3. Press Return and wait until the Terminal shows its prompt again, ending in `%` on the Mac or `$` on the Pi.

If a command asks for your **password**, type it even though nothing appears on the screen, then press Return.

---

## Part 1: The Mac

The Mac creates the strips. It needs Python, the photo booth software, and the AI model.

### 1.1 Download the photo booth
```bash
git clone https://github.com/archilab/Fotobox.git ~/photobox-server
```
This creates the folder `photobox-server` in your home folder. If macOS asks to install "command line developer tools", click **Install**, wait until it finishes, and run the command again.

> **Why not in Documents?** macOS doesn't let programs that start automatically read the Documents, Desktop or Downloads folders. Keep the photo booth in `~/photobox-server`.

### 1.2 Set up the Mac with one command
```bash
cd ~/photobox-server && scripts/setup_mac.sh
```
This installs the booth software and the AI tool, downloads the AI model (about 15 GB, which can take a while), writes the settings, keeps visitors' photos out of Time Machine backups, and makes the server and dashboard start by themselves.

If it says Python is missing or too old, install the latest Python from [python.org/downloads](https://www.python.org/downloads/), open a new Terminal, and run the command again.

### 1.3 Answer its three questions
Only you can decide these, so the script stops and asks:

1. **Consent notice.** Visitors read this text on the booth before their photo is taken. It opens in TextEdit. Replace **both** `TODO-OPERATOR` entries, the **minimum age** of visitors, such as `16`, and **who is responsible**, with name and a contact such as an email address. Save with ⌘S, as **plain text**, and press ENTER in the Terminal.
   > The example text is a starting point, not legal advice. Have it checked before you use the booth with the public. You can change it later in the dashboard.
2. **Counterparts.** Every strip needs a second person next to the visitor. The script creates two invented people with the AI model, about a minute each, and shows them. Look at them, and type `yes` if you may use them. Use only pictures you are allowed to use; never photos of real people without their permission. To add your own later:
   ```bash
   cd ~/photobox-server && .venv/bin/python -m photobox_server.admin seed add ~/PICTURE.png --rights-reference YOUR-REFERENCE --attest-rights
   ```
3. **Dashboard password.** Choose one of at least 10 characters. The dashboard is a web page on the Mac where you watch the booth and change things.

If macOS asks whether **Python may accept incoming network connections**, click **Allow**, or the Pi can't reach the Mac.

**You should see** `Finished.` and the Mac's address, such as `192.168.1.24`, followed by the exact command for setting up the Pi. Copy that command for Part 2. If a `Still to do:` list follows, finish those points. ✅ The Mac is ready.

Open **<http://127.0.0.1:8881>** in a browser on the Mac and log in. It only works on this Mac, on purpose.

> Something went wrong? Read the line starting with `Stopped:`, fix that, and run the command again. Parts that are already done are skipped, your settings are kept and a running server is left running. To only see what is there, without changing anything, add `--check`.

**Optional: the pool.** If visitors should be able to share their photo with later visitors for 24 hours, switch it on later on the dashboard's Pool page.

---

## Part 2: The Raspberry Pi

### 2.1 Prepare the memory card
1. Install **Raspberry Pi Imager** from [raspberrypi.com/software](https://www.raspberrypi.com/software/) on your Mac.
2. Choose **Raspberry Pi 5**, then **Raspberry Pi OS (64-bit)**. The *Lite* version is enough.
3. Before writing, open the **settings** (OS customisation) and set:
   - **Hostname:** `fotobox`
   - **User name:** `admin` (exactly this, because the booth's files expect it) and a password you'll remember
   - **WiFi:** your network and its password
   - **Services:** turn on **SSH** with password login
4. Write the card, put it in the Pi, connect the webcam, screen and keyboard, and switch the Pi on. The first start takes a few minutes.

### 2.2 Set up the Pi with one command
Wait until the Pi has finished its first start. Then run this **on the Mac**. It's the command the Mac's installer printed at the end; `MAC_IP_ADDRESS` is the address it showed:
```bash
cd ~/photobox-server && scripts/setup_pi.sh admin@fotobox.local --server MAC_IP_ADDRESS --reboot
```
The first time, type `yes` to trust the Pi. The Pi's password is asked for, up to three times.

This installs everything the booth needs: the system packages, the booth program, voice and person detection. It takes about 15 minutes, mostly to build the speech program. **You should see** `Finished.` at the end, and then the Pi restarts by itself. ✅ The Pi is ready. Go to the booth itself for the next part.

> Voice and person detection are installed but **not switched on**. You choose them in the setup menu in Part 3.

> If `fotobox.local` isn't found, look up the Pi's IP address in your router's device list and use it instead, such as `admin@192.168.1.140`.

> Something went wrong? Read the line starting with `Stopped:`, fix that, and run the command again. Parts that are already done are skipped. To only see what is there, without changing anything, use `--check` in place of `--server MAC_IP_ADDRESS --reboot`.

---

## Part 3: Set up the booth with its keyboard

After the restart, the booth's screen shows **FOTOBOX – Starting**. Because nothing is set up yet, the **setup menu** opens by itself. In the menu, press a **number** to choose, **ENTER** to confirm, **ESC** to go back, and **BACKSPACE** to delete a character.

1. **Choose a PIN** of 4 to 8 digits, and enter it twice. It keeps visitors out of the menu. Remember it.
2. **2 Server address:** the installer has already filled this in. Change it only if the Mac's address is different now.
3. **3 Pair with server:** this links the booth to your Mac.
   - On the Mac, open the dashboard (<http://127.0.0.1:8881>), go to **Boxes**, type the name `pi-01` and click **Show pairing code**.
   - Type the 6-digit code at the booth and press ENTER. **You should see** "Paired as pi-01". The code works once, within 5 minutes.
4. **4 Camera:** choose the webcam. A test picture appears for 5 seconds; it isn't stored.
5. **5 Test everything:** every line should start with **OK**.
6. **6 Start photo booth.** The screen shows **FOTOBOX – Press ENTER to start**. ✅

Two more entries are optional, and both can wait:
- **7 Microphone** chooses the microphone for voice messages. It records nothing here.
- **8 Person detection** lets the camera notice that someone is at the box. Put up the sign from `person-detection-sign.example.txt` first, where people see it before they come into the camera's view.

The WiFi was already set when you prepared the memory card. To change it later, use **1 WiFi** in this menu. To open the menu again, press **Ctrl+Alt+S** on the start screen, or **M** during the first 10 seconds after the booth restarts.

---

## Part 4: Your first photo

1. Sit in front of the camera and press **ENTER**. The consent notice appears.
2. Press **ENTER** again to agree. After 3-2-1 the photo is taken.
3. Watch the timer. After about three minutes, your strip appears for 20 seconds.
4. On the Mac, the dashboard's **Overview** shows the booth as **online**, and **Strips** shows your strip.

🎉 **Done.** Read the [operator guide](docs/OPERATOR_GUIDE.md) to learn about daily use, deleting a visitor's pictures and changing the stories.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| The booth's screen stays black or the text is tiny | Check that the screen is on and set to the right input. Restart the Pi by unplugging it for a few seconds. |
| The setup menu says **Server: not reachable** | Check the Mac's IP address (step 1.3), that the Mac is awake, and that you allowed Python's network connections. |
| The booth says **"not ready"** | The consent notice still contains `TODO-OPERATOR` (step 1.3), or the Mac's server isn't running. |
| **Pairing failed** | The code is older than 5 minutes, or was already used. Show a new one in the dashboard. |
| The dashboard page doesn't open | Use exactly `http://127.0.0.1:8881`, on the Mac itself. |
| `Bootstrap failed: 5` in the Terminal | That service is already running. Nothing to do. |
| `Operation not permitted` in a log | The photo booth folder is inside Documents. Install it in `~/photobox-server` (step 1.1). |

More problems and their solutions are in the [operator guide](docs/OPERATOR_GUIDE.md#troubleshooting).

---

## Updating later

On the Mac:
```bash
cd ~/photobox-server && git pull && .venv/bin/pip install -e '.[server-ai]' && launchctl kickstart -k gui/$(id -u)/com.photobox.server && launchctl kickstart -k gui/$(id -u)/com.photobox.dashboard
```
On the Pi, after `ssh admin@fotobox.local`:
```bash
cd ~/photobox && git pull && .venv/bin/pip install -e '.[client-camera]' && sudo systemctl restart photobox-kiosk
```
Your settings, photos and stories are kept.
