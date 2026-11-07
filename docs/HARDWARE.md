# Hardware / deployment

## Pi (current)
- Raspberry Pi 5 (single client), appropriate power supply, Raspberry Pi OS 64-bit
- Standard UVC USB webcam connected via USB (check `lsusb`, `v4l2-ctl --list-devices`, `ls -l /dev/v4l/by-id`)
- CLI only, started over local console or SSH. The attached screen is used as a console only: no screen/touch UI and no printing.
- Optional `sudo apt install v4l-utils python3-opencv`; environment versions vary; use venv with site-package strategy only after testing.
- Prefer camera stable path rather than index for multiple video devices (M1 implementation).

## MacBook Pro M3, 64GB (development host, current)
- Runs the API during development on the same LAN as the Pi. AI timings and memory limits measured here do not carry over to the Mac Studio and must be measured again there.

## Mac Studio M3 Ultra, 256GB (target host, later)
- Central FastAPI host and future local AI image-generation worker
- Use loopback for local demo; private authenticated network before remote Pi use
- AI model/provider NOT yet selected; do not assume CoreML/MLX/ComfyUI support for arbitrary image models without checking compatibility.

## Future (excluded)
- touchscreen, thermoprinter, additional Raspberry Pis and synchronized matchmaking
