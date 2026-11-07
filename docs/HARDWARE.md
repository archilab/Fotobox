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
python -m uvicorn photobox_server.app:app --host "$(ipconfig getifaddr en0)" --port 8000
```
On the Pi, store the token in a file only you can read:
```bash
install -m 600 /dev/null ~/.photobox.token && nano ~/.photobox.token
```
Then point the camera at a synthetic scene or an authorized subject and run the acceptance test. It uploads 20 captures, and you check the saved orientation sample by eye:
```bash
PHOTOBOX_DEVICE_TOKEN="$(cat ~/.photobox.token)" scripts/m1_acceptance.sh http://MAC_LAN_IP:8000 CAMERA_NAME --attest-documented-consent --rotate 0
```
Moving to NetBird or Tailscale later changes only the server address on both machines.

### Running the server permanently on the Mac
`scripts/run_server.sh` starts the server on the Mac's current LAN address with the settings in `.env`. macOS does not let launchd agents read `~/Documents`, `~/Desktop` or `~/Downloads`, so run the service from a copy outside them, for example `~/photobox-server` with its own `.venv`, `.env`, `consent-notice.txt` and `storage/`. To start it at login, install the launchd agent from that folder:
```bash
sed -e "s|REPOSITORY|$PWD|g" -e "s|HOME|$HOME|g" deploy/mac/com.photobox.server.plist > ~/Library/LaunchAgents/com.photobox.server.plist && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.photobox.server.plist
```
To stop it: `launchctl bootout gui/$(id -u)/com.photobox.server`. The log is `~/Library/Logs/photobox-server.log`. macOS may ask once whether Python may accept incoming connections. Exclude `storage/` from Time Machine before real portraits are stored: `tmutil addexclusion storage`.

### Keyboard kiosk on the Pi's screen (ADR-020)
On the Mac, copy `consent-notice.example.txt` to `consent-notice.txt` in the folder the server starts from, and replace every `TODO-OPERATOR`. Until then the kiosk shows "not ready". On the Pi, write the settings and install the service, which replaces the login prompt on tty1:
```bash
printf 'PHOTOBOX_SERVER=http://MAC_LAN_IP:8000\nPHOTOBOX_CAMERA=/dev/v4l/by-id/CAMERA_NAME\n' > ~/.photobox-kiosk.env
```
```bash
sudo cp ~/photobox/deploy/pi/photobox-kiosk.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now photobox-kiosk
```
To get the login prompt back: `sudo systemctl disable --now photobox-kiosk && sudo systemctl start getty@tty1`. To try the kiosk without the camera, run `python -m photobox_client.kiosk --server URL --token-file ~/.photobox.token --test-image FILE --once` in a terminal.

### Tested setup (2026-11-07)
`scripts/m1_acceptance.sh` passed once, with 20 of 20 uploads, on this setup:
- Raspberry Pi 5 (4GB), Debian 13 (trixie), 64-bit, Python 3.13.5, OpenCV 5.0.0
- Logitech BRIO over USB at its stable `video-index0` name, default 640×480, `--rotate 0`
- MacBook server on its LAN address, with a scene containing no people

The uploaded JPEGs had no EXIF data, and the device token did not appear in the server log or database. In that test image the camera focused on a cable in the foreground, so check focus and resolution before relying on image quality. The BRIO also accepts 1280×720 and 1920×1080, but captures at those sizes have not been tested.

## MacBook Pro M3, 64GB (development host, current)
- For real generation, install the server with `pip install -e '.[server-ai]'` and mflux with `uv tool install mflux` and set `PHOTOBOX_PROVIDER=mflux-flux2`. If the server does not find it, set `PHOTOBOX_MFLUX_COMMAND` to its full path, usually `~/.local/bin/mflux-generate-flux2-edit`. The server runs mflux offline, so the model must already be downloaded (about 15 GB) by one run of mflux with network access. Measured on this machine: about 40-47 s per frame and about 10.3 GB peak MLX memory, so about 3 minutes per 4-frame strip. Exclude the server's storage folder and evaluation results from Time Machine before using real portraits.
- Runs the API during development on the same LAN as the Pi. AI timings and memory limits measured here do not carry over to the Mac Studio and must be measured again there.

## Mac Studio M3 Ultra, 256GB (target host, later)
- Central FastAPI host and local AI image-generation worker
- Use loopback for local demo; private authenticated network before remote Pi use
- FLUX.2 klein 4B through mflux is selected (ADR-014), but has not run on this machine yet.

## Future (excluded)
- touchscreen, thermoprinter, additional Raspberry Pis and synchronized matchmaking
