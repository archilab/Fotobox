# Hardware / deployment

## Pi (current)
- Raspberry Pi 5 (single client), appropriate power supply, Raspberry Pi OS 64-bit
- Standard UVC USB webcam connected via USB (check `lsusb`, `v4l2-ctl --list-devices`, `ls -l /dev/v4l/by-id`)
- CLI only, started over local console or SSH. The attached screen is used as a console only: no screen/touch UI and no printing.
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

## MacBook Pro M3, 64GB (development host, current)
- Runs the API during development on the same LAN as the Pi. AI timings and memory limits measured here do not carry over to the Mac Studio and must be measured again there.

## Mac Studio M3 Ultra, 256GB (target host, later)
- Central FastAPI host and future local AI image-generation worker
- Use loopback for local demo; private authenticated network before remote Pi use
- AI model/provider NOT yet selected; do not assume CoreML/MLX/ComfyUI support for arbitrary image models without checking compatibility.

## Future (excluded)
- touchscreen, thermoprinter, additional Raspberry Pis and synchronized matchmaking
