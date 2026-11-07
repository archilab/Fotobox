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
- [ ] A Mac with Apple Silicon (M1 or newer) and about 20 GB of free space
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

### 1.1 Check Python
```bash
python3 --version
```
**You should see** `Python 3.11` or higher. If you see an older number, or macOS offers to install developer tools, install the latest Python from [python.org/downloads](https://www.python.org/downloads/), then close the Terminal, open it again and check once more.

### 1.2 Install uv, a helper for the AI tool
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
**Afterwards** close the Terminal and open a new one, so it finds the new tool.

### 1.3 Download the photo booth
```bash
git clone https://github.com/archilab/Fotobox.git ~/photobox-server
```
This creates the folder `photobox-server` in your home folder. If macOS asks to install "command line developer tools", click **Install**, wait until it finishes, and run the command again.

> **Why not in Documents?** macOS doesn't let programs that start automatically read the Documents, Desktop or Downloads folders. Keep the photo booth in `~/photobox-server`.

### 1.4 Install the software
```bash
cd ~/photobox-server && python3 -m venv .venv && .venv/bin/pip install --upgrade pip && .venv/bin/pip install -e '.[server-ai]'
```
This takes a few minutes. **You should see** `Successfully installed …` near the end.

### 1.5 Install the AI tool and download the model
```bash
uv tool install mflux
```
Now make one test picture. The first time, this downloads the AI model, about 15 GB, which can take a while:
```bash
~/.local/bin/mflux-generate-flux2 --model flux2-klein-4b -q 8 --prompt "a red apple on a wooden table" --steps 4 --width 512 --height 512 --output ~/apple-test.png
```
**You should see** `Image saved successfully` at the end. The photo booth later uses the model without the internet, which is why this download has to happen once by hand. You can delete `~/apple-test.png`.

### 1.6 Write the settings
This copies the example settings and switches them to the real AI model:
```bash
cd ~/photobox-server && sed -e 's/^PHOTOBOX_PROVIDER=mock/PHOTOBOX_PROVIDER=mflux-flux2/' -e 's/^PHOTOBOX_DEVELOPER_MODE=true/PHOTOBOX_DEVELOPER_MODE=false/' -e "s|^# PHOTOBOX_MFLUX_COMMAND=.*|PHOTOBOX_MFLUX_COMMAND=$HOME/.local/bin/mflux-generate-flux2-edit|" .env.example > .env && chmod 600 .env
```
No output means it worked. All other settings can stay as they are; the [operator guide](docs/OPERATOR_GUIDE.md#settings) explains them.

### 1.7 Write your consent notice
Visitors read this text on the booth before their photo is taken.
```bash
cd ~/photobox-server && cp consent-notice.example.txt consent-notice.txt && open -e consent-notice.txt
```
The text opens in TextEdit. Replace **both** `TODO-OPERATOR` entries:
- the **minimum age** of visitors, such as `16`
- **who is responsible**, with name and a contact such as an email address

Then save with ⌘S. If TextEdit asks about the format, choose **plain text**.

> The example text is a starting point, not legal advice. Have it checked before you use the booth with the public. You can change it later in the dashboard.

### 1.8 Add counterparts
Every strip needs a second person next to the visitor. Create a few invented people with the AI model; this takes about a minute each:
```bash
~/.local/bin/mflux-generate-flux2 --model flux2-klein-4b -q 8 --prompt "Studio portrait photograph of a fictional woman in her sixties with short grey hair and round glasses, dark green sweater, looking at the camera, plain grey background" --steps 4 --width 768 --height 1024 --output ~/counterpart-1.png
```
```bash
~/.local/bin/mflux-generate-flux2 --model flux2-klein-4b -q 8 --prompt "Studio portrait photograph of a fictional man in his thirties with a short beard, mustard yellow shirt, looking at the camera, plain light background" --steps 4 --width 768 --height 1024 --output ~/counterpart-2.png
```
Look at them, then add them to the photo booth:
```bash
cd ~/photobox-server && for f in ~/counterpart-1.png ~/counterpart-2.png; do .venv/bin/python -m photobox_server.admin seed add "$f" --rights-reference FICTIONAL-AI --attest-rights; done
```
**You should see** `Seed imported: …` twice. Use only pictures you are allowed to use; never photos of real people without their permission.

### 1.9 Start the photo booth server automatically
```bash
cd ~/photobox-server && sed -e "s|REPOSITORY|$PWD|g" -e "s|HOME|$HOME|g" deploy/mac/com.photobox.server.plist > ~/Library/LaunchAgents/com.photobox.server.plist && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.photobox.server.plist
```
If macOS asks whether **Python may accept incoming network connections**, click **Allow**, or the Pi can't reach the Mac.

Write down the Mac's IP address; you'll need it at the booth:
```bash
ipconfig getifaddr en0
```
**You should see** something like `192.168.1.24`. If nothing appears, the Mac is probably on a cable: try `ipconfig getifaddr en1`.

### 1.10 Set up the dashboard
The dashboard is a web page on the Mac where you watch the booth and change things. Choose a password of at least 10 characters:
```bash
cd ~/photobox-server && .venv/bin/python -m photobox_server.admin dashboard set-password
```
Then start it automatically:
```bash
cd ~/photobox-server && sed -e "s|REPOSITORY|$PWD|g" -e "s|HOME|$HOME|g" deploy/mac/com.photobox.dashboard.plist > ~/Library/LaunchAgents/com.photobox.dashboard.plist && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.photobox.dashboard.plist
```
Open **<http://127.0.0.1:8001>** in Safari or another browser on the Mac, and log in. It only works on this Mac, on purpose.

### 1.11 Keep visitors' photos out of backups
```bash
tmutil addexclusion ~/photobox-server/storage
```
Photos are deleted after an hour, but a Time Machine backup would keep a copy. This prevents that.

### 1.12 Check the Mac
```bash
curl -s http://$(ipconfig getifaddr en0):8000/api/v1/health
```
**You should see** `{"status":"ok","provider":"mflux-flux2",…}`. ✅ The Mac is ready.

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

### 2.2 Connect to the Pi from your Mac
```bash
ssh admin@fotobox.local
```
The first time, type `yes` to trust the Pi, then the Pi's password. **You should see** a prompt like `admin@fotobox:~ $`. The following commands in Part 2 run **on the Pi**, in this window.

> If `fotobox.local` isn't found, look up the Pi's IP address in your router's device list and use it instead, such as `ssh admin@192.168.1.140`.

### 2.3 Install what the booth needs
```bash
sudo apt update && sudo apt install -y git python3-venv polkitd
```

### 2.4 Download and install the photo booth
```bash
git clone https://github.com/archilab/Fotobox.git ~/photobox && cd ~/photobox && python3 -m venv .venv && .venv/bin/pip install -e '.[client-camera]'
```
This takes a few minutes. **You should see** `Successfully installed …` at the end.

> If GitHub asks for a user name, the repository is private: sign in with an access token, or copy the folder over from the Mac as described in the [developer guide](docs/DEVELOPMENT.md#deploying-a-working-copy-without-git).

### 2.5 Let the booth manage WiFi and start by itself
This lets the setup menu change WiFi:
```bash
sudo cp ~/photobox/deploy/pi/50-photobox-network.rules /etc/polkit-1/rules.d/
```
This makes the booth start on the screen at every boot, instead of the login prompt:
```bash
sudo cp ~/photobox/deploy/pi/photobox-kiosk.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable photobox-kiosk
```
Now restart the Pi:
```bash
sudo reboot
```
Your SSH connection closes; that's expected. ✅ The Pi is ready. Go to the booth itself for the next part.

---

## Part 3: Set up the booth with its keyboard

After the restart, the booth's screen shows **FOTOBOX – Starting**. Because nothing is set up yet, the **setup menu** opens by itself. In the menu, press a **number** to choose, **ENTER** to confirm, **ESC** to go back, and **BACKSPACE** to delete a character.

1. **Choose a PIN** of 4 to 8 digits, and enter it twice. It keeps visitors out of the menu. Remember it.
2. **2 Server address:** type the Mac's IP address from step 1.9, such as `192.168.1.24`, then ENTER.
3. **3 Pair with server:** this links the booth to your Mac.
   - On the Mac, open the dashboard (<http://127.0.0.1:8001>), go to **Boxes**, type the name `pi-01` and click **Show pairing code**.
   - Type the 6-digit code at the booth and press ENTER. **You should see** "Paired as pi-01". The code works once, within 5 minutes.
4. **4 Camera:** choose the webcam. A test picture appears for 5 seconds; it isn't stored.
5. **5 Test everything:** every line should start with **OK**.
6. **6 Start photo booth.** The screen shows **FOTOBOX – Press ENTER to start**. ✅

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
| The setup menu says **Server: not reachable** | Check the Mac's IP address (step 1.9), that the Mac is awake, and that you allowed Python's network connections. |
| The booth says **"not ready"** | The consent notice still contains `TODO-OPERATOR` (step 1.7), or the Mac's server isn't running. |
| **Pairing failed** | The code is older than 5 minutes, or was already used. Show a new one in the dashboard. |
| The dashboard page doesn't open | Use exactly `http://127.0.0.1:8001`, on the Mac itself. |
| `Bootstrap failed: 5` in the Terminal | That service is already running. Nothing to do. |
| `Operation not permitted` in a log | The photo booth folder is inside Documents. Install it in `~/photobox-server` (step 1.3). |

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
