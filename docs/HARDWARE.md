# Hardware / deployment

## Pi (current)
- Raspberry Pi 5 (single client), appropriate power supply, Raspberry Pi OS 64-bit
- Standard UVC USB webcam connected via USB (check `lsusb`, `v4l2-ctl --list-devices`, `ls -l /dev/v4l/by-id`)
- The attached screen and USB keyboard run the kiosk (ADR-020); no touchscreen and no printing. The CLI remains available over SSH.
- Optional `sudo apt install v4l-utils python3-opencv`; environment versions vary; use venv with site-package strategy only after testing.
- Use the stable name from `camera-list` rather than an index, because indexes can change after a reboot or replug. Webcams often list two entries; `video-index0` is normally the capture device.
- `pip install -e '.[client-camera]'` installs `opencv-python-headless`, which has no display dependency.

## Connecting the Pi to the development Mac (trusted LAN only, see ADR-010)
On the Mac, register the Pi and start the server on the Mac's LAN address rather than `0.0.0.0`. macOS may ask whether to allow incoming connections.
```bash
python -m photobox_server.admin device add pi-01
```
```bash
python -m uvicorn photobox_server.app:app --host "$(ipconfig getifaddr en0)" --port 8880
```
On the Pi, store the token in a file only you can read:
```bash
install -m 600 /dev/null ~/.photobox.token && nano ~/.photobox.token
```
Then point the camera at a synthetic scene or an authorized subject and run the acceptance test. It uploads 20 captures, and you check the saved orientation sample by eye:
```bash
PHOTOBOX_DEVICE_TOKEN="$(cat ~/.photobox.token)" scripts/m1_acceptance.sh http://MAC_LAN_IP:8880 CAMERA_NAME --attest-documented-consent --rotate 0
```
Moving to NetBird or Tailscale later changes only the server address on both machines.

### Running the server permanently on the Mac
`scripts/run_server.sh` starts the server on the Mac's current LAN address with the settings in `.env`. macOS does not let launchd agents read `~/Documents`, `~/Desktop` or `~/Downloads`, so run the service from a copy outside them, for example `~/photobox-server` with its own `.venv`, `.env`, `consent-notice.txt` and `storage/`. To start it at login, install the launchd agent from that folder:
```bash
sed -e "s|REPOSITORY|$PWD|g" -e "s|HOME|$HOME|g" deploy/mac/com.photobox.server.plist > ~/Library/LaunchAgents/com.photobox.server.plist && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.photobox.server.plist
```
To stop it: `launchctl bootout gui/$(id -u)/com.photobox.server`. The log is `~/Library/Logs/photobox-server.log`. macOS may ask once whether Python may accept incoming connections. Exclude `storage/` from Time Machine before real portraits are stored: `tmutil addexclusion storage`.

### Keyboard kiosk on the Pi's screen (ADR-020)
A new Pi is set up with `scripts/setup_pi.sh` (ADR-033, `INSTALL.md` step 2.2). The manual steps below show what it does.

On the Mac, copy `consent-notice.example.txt` to `consent-notice.txt` in the folder the server starts from, and replace every `TODO-OPERATOR`. Until then the kiosk shows "not ready". On the Pi, write the settings and install the service, which replaces the login prompt on tty1:
```bash
printf 'PHOTOBOX_SERVER=http://MAC_LAN_IP:8880\nPHOTOBOX_CAMERA=/dev/v4l/by-id/CAMERA_NAME\n' > ~/.photobox-kiosk.env
```
```bash
sudo cp ~/photobox/deploy/pi/photobox-kiosk.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now photobox-kiosk
```
The screen is a browser since ADR-028: `deploy/pi/run-kiosk.sh` starts the kiosk with Chromium under Cage at 1920 x 1080 (`sudo apt install --no-install-recommends cage chromium wlr-randr`), and starts the text kiosk instead if the browser cannot run. Measured on the Pi 5 with 4 GB: the page is ready 1 to 3 s after the browser starts, with about 3.4 GB of memory still available. For unattended running, `sudo sh ~/photobox/deploy/pi/install-autonomy.sh` starts the booth at boot and installs the watchdogs (ADR-032). To get the login prompt back: `sudo systemctl disable --now photobox-kiosk && sudo systemctl start getty@tty1`. To try the kiosk without the camera, run `python -m photobox_client.kiosk --server URL --token-file ~/.photobox.token --test-image FILE --once` in a terminal.

### Tested setup (2026-11-07)
`scripts/m1_acceptance.sh` passed once, with 20 of 20 uploads, on this setup:
- Raspberry Pi 5 (4GB), Debian 13 (trixie), 64-bit, Python 3.13.5, OpenCV 5.0.0
- Logitech BRIO over USB at its stable `video-index0` name, default 640×480, `--rotate 0`
- MacBook server on its LAN address, with a scene containing no people

The uploaded JPEGs had no EXIF data, and the device token did not appear in the server log or database. In that test image the camera focused on a cable in the foreground, so check focus and resolution before relying on image quality. The BRIO also accepts 1280×720 and 1920×1080, but captures at those sizes have not been tested.

## MacBook Pro M3, 64GB (development host)
- For real generation, install the server with `pip install -e '.[server-ai]'` and mflux with `uv tool install mflux` and set `PHOTOBOX_PROVIDER=mflux-flux2`. If the server does not find it, set `PHOTOBOX_MFLUX_COMMAND` to its full path, usually `~/.local/bin/mflux-generate-flux2-edit`. The server runs mflux offline, so the model must already be downloaded (about 15 GB) by one run of mflux with network access. Measured on this machine: about 40-47 s per frame and about 10.3 GB peak MLX memory, so about 3 minutes per 4-frame strip. Exclude the server's storage folder and evaluation results from Time Machine before using real portraits.
- Runs the API during development on the same LAN as the Pi. AI timings and memory limits measured here do not carry over to the Mac Studio and must be measured again there.

## Mac Studio M3 Ultra, 256GB (server for the booth since 2026-11-08)
- Central FastAPI host and local AI image-generation worker
- Use loopback for local demo; private authenticated network before remote Pi use
- FLUX.2 klein 4B through mflux is selected (ADR-014) and runs on this machine; see the setup below.

### Setting up the Mac Studio
Measured on 2026-11-08 on the Mac Studio (M3 Ultra, 256 GB) with the same code, configuration only: all tests pass, and `scripts/benchmark_provider.py` made 4 FLUX.2 frames in 79 s (19.8 s per frame, 10.3 GB peak memory), with drawn test faces. A real strip on the MacBook Pro took about 270 s. Quality was not compared.

1. Copy the code to `~/photobox-server`, as in `docs/DEVELOPMENT.md`.
2. Create the environment with Python 3.13 and `.[dev,server-ai]`, and install the image tool with `uv tool install --python 3.12 mflux==0.24.0`.
3. Copy the model folder `~/.cache/huggingface/hub/models--black-forest-labs--FLUX.2-klein-4B` from a machine that has it, following links (`rsync -aL`, about 30 GB), or download it there.
4. In `.env`, set `PHOTOBOX_PROVIDER=mflux-flux2`, `PHOTOBOX_DEVELOPER_MODE=false` and `PHOTOBOX_MFLUX_COMMAND` to the full path. The Studio is on WiFi (`en1`), so also set `PHOTOBOX_INTERFACE=en1`, or use Ethernet.
5. Install the launchd services as for the MacBook. launchd cannot read `~/Documents`, so keep the copy in `~/photobox-server`.
6. Move what the operator made with `scripts/content_transfer.py export FOLDER` on the old server and `import FOLDER` on the new one, each run in the server's folder. It copies seed images, notices, prompt sets and blocked words. It never copies visitors' photos, strips, sessions, consent records, the audit trail or devices, so keep the old server running until its pool photos have expired, because earlier withdrawal codes work only there.

A complete strip through the API on the Studio, with a drawn test face and FLUX.2, took 78 s on 2026-11-08. A drawn face says nothing about image quality with real photos. One real visit at the booth the same day took 87 s of generation, over WiFi, with one attempt and no errors.

Before relying on the Studio unattended, connect Ethernet with an address reserved in the router, then set `PHOTOBOX_INTERFACE=en0`, restart the server and enter the new address in the booth's setup menu. Turn on restart after power failure (`sudo pmset -a autorestart 1`) and automatic login, because the services start only after login.

## Future (excluded)
- touchscreen, thermoprinter, additional Raspberry Pis and synchronized matchmaking
