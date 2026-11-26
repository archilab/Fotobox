# Wiring and configuring a booth

This guide covers the Raspberry Pi side of one booth: what to connect where, and which settings switch each part on. It follows `newbox`, the booth everything was tested on. Install the software first ([INSTALL.md](../INSTALL.md), Part 2); come back here for the button, the lights, the distance sensor, sound and the printer.

**Before touching the header, shut the Pi down and unplug its power** (`sudo poweroff`, wait until the green light stops blinking). A wire on the wrong pin can destroy the Pi. The Pi's pins work at **3.3 V**: never connect 5 V to a GPIO pin.

## Parts

| Part | Used on newbox | Connected to |
|---|---|---|
| Raspberry Pi 5, 4 GB, with the official 27 W power supply | Raspberry Pi 5 Model B Rev 1.0, Raspberry Pi OS 13 (trixie), 64-bit | – |
| 7-inch HDMI panel, 1280 x 800 | as named | first HDMI port (HDMI0, next to the USB-C power), shown as `HDMI-A-1` and sound card `vc4hdmi0` |
| USB webcam with microphone (UVC) | Logitech BRIO | a blue USB 3 port |
| USB receipt printer, ESC/POS, 80 mm (optional) | Netum NT-H861, reports itself as Epson TM-m30II | any USB port; appears as `/dev/usb/lp0` |
| Illuminated push button with RGB ring | Metzler P30-TF-RGB | button: GPIO header directly; ring: through the ULN2803A |
| ULN2803A, 8 Darlington drivers in a DIP-18 package | as named | GPIO header, button ring |
| VL53L0X time-of-flight distance sensor on a breakout board (optional) | as named | GPIO header, I2C |
| Series resistors for the ring, if the button has none built in | on newbox | between ULN2803A outputs and the ring |

## The pins used

Pin 1 is at the end of the header farthest from the USB and Ethernet ports, and has a square solder pad on the underside. Odd pins are the row towards the middle of the board, even pins the row along the edge. `pinout` on the Pi prints the same map.

```
            3.3 V  [ 1] [ 2]  5 V  ............ button ring common (+)
  sensor SDA GPIO2 [ 3] [ 4]  5 V
  sensor SCL GPIO3 [ 5] [ 6]  GND  ............ sensor GND
                   [ 7] [ 8]
  ULN2803A pin 9   [ 9] [10]
             GND
                   [11] [12]  GPIO18 .......... ULN2803A IN1 (red)
                   [13] [14]  GND
                   [15] [16]  GPIO23 .......... ULN2803A IN2 (green)
  sensor VIN 3.3 V [17] [18]  GPIO24 .......... ULN2803A IN3 (blue)
                   [19] [20]  GND  ............ button, second terminal
                   [21] [22]  GPIO25 .......... button, NO terminal
                    ...
```

| Pin | Name | Goes to |
|---|---|---|
| 1 or 17 | 3.3 V | distance sensor VIN |
| 2 | 5 V | button ring common anode (C) |
| 3 | GPIO2 (SDA) | distance sensor SDA |
| 5 | GPIO3 (SCL) | distance sensor SCL |
| 6 | GND | distance sensor GND |
| 9 | GND | ULN2803A pin 9 (GND) |
| 12 | GPIO18 | ULN2803A pin 1 (IN1), red |
| 16 | GPIO23 | ULN2803A pin 2 (IN2), green |
| 18 | GPIO24 | ULN2803A pin 3 (IN3), blue |
| 20 | GND | button, second terminal |
| 22 | GPIO25 | button, NO (normally open) terminal |

Any GND pin (6, 9, 14, 20, 25, 30, 34, 39) will do; the ones above are simply close to their partners.

## The push button

The button is a plain switch between **GPIO25 (pin 22)** and **GND (pin 20)**, using its normally open (NO) contact. No resistor is needed: the booth switches on the Pi's internal pull-up, so the pin reads 1 at rest and 0 while the button is held (ADR-036). It does not go through the ULN2803A.

Measured on newbox: the contact bounces for less than 1 ms when pressed; the release was clean. The booth counts one press per push, and on the start screen a 5-second hold is a signal of its own (the hidden video link, ADR-045).

## The status lights: the button's RGB ring through a ULN2803A

The ring has one common **anode** (C, +) and one **cathode** per colour (R, G, B). The Pi cannot drive it directly, so a ULN2803A switches each cathode to ground: when a GPIO pin goes high (3.3 V), the matching output of the ULN2803A conducts and that colour lights. This is why the settings speak of lights that are "lit when high".

```
   Pi 5 V (pin 2) ──────────────────────────────► C   (common anode of the ring)

   GPIO18 (pin 12) ──► IN1 (1)   OUT1 (18) ──[R1]──► R   cathode
   GPIO23 (pin 16) ──► IN2 (2)   OUT2 (17) ──[R2]──► G   cathode
   GPIO24 (pin 18) ──► IN3 (3)   OUT3 (16) ──[R3]──► B   cathode
   Pi GND (pin 9)  ──► GND (9)   COM  (10)   not connected

                ULN2803A, notch up, pins 1–9 down the left, 10–18 up the right
```

- **Orientation:** with the notch at the top, pin 1 is top left and pin 9 bottom left; pin 10 is bottom right and pin 18 top right. Inputs IN1–IN8 are pins 1–8, outputs OUT1–OUT8 are pins 18 down to 11.
- **GND (pin 9) must be connected to the Pi's GND**, or nothing switches.
- **COM (pin 10) stays unconnected.** It only matters for coils such as relays or motors.
- IN4–IN8 and OUT4–OUT8 stay free.
- **Series resistors.** Unless the ring already has resistors for 5 V built in (check the button's data sheet for its LED voltage and current), put one resistor in each colour's line. As a starting point, for about 10–15 mA each, with roughly 1 V lost in the ULN2803A: about **150 Ω for red** and **82–100 Ω for green and blue**. Use more if a colour is too bright, never less than the data sheet allows. newbox has series resistors.
- **5 V from the Pi** is fine for a small LED ring: three colours at 15 mA are under 50 mA. For anything brighter, use a separate 5 V supply for C and connect its GND to the Pi's GND.

Violet is red at a quarter and blue full, mixed with software PWM; the lights fade through lgpio (`python3-lgpio`, installed by `install.sh`, ADR-044).

## The distance sensor (VL53L0X, optional)

It tells the booth whether someone stands in front of it, without a camera (ADR-046).

| Sensor breakout | Pi pin |
|---|---|
| VIN | 1 (3.3 V) |
| GND | 6 (GND) |
| SDA | 3 (GPIO2, SDA) |
| SCL | 5 (GPIO3, SCL) |
| XSHUT, GPIO1 (if present) | not connected |

Mount it at the front of the booth, pointing straight out at about chest height, with nothing in its view closer than the range you will set: it sees a cone of about 25 degrees and reaches about 1.2 m. On newbox it also saw the operator sitting at the desk next to the booth, so keep tables and chairs out of that cone.

Switch on the Pi's I2C bus once, then restart:
```bash
sudo raspi-config nonint do_i2c 0 && sudo reboot
```
Check that the sensor answers at address 29 (hexadecimal):
```bash
sudo apt install -y i2c-tools && /usr/sbin/i2cdetect -y 1
```
The table should show `29`. The user `admin` is already in the group `i2c`, which may use `/dev/i2c-1`.

## Screen, camera, sound and printer

- **Screen:** the panel on the first HDMI port. The kiosk lays its screen out for 1280 x 800 (ADR-038).
- **Camera:** any UVC webcam; choose it in the setup menu under **4 Camera**.
- **Microphone:** the BRIO's microphone, chosen under **7 Microphone** (newbox: `plughw:CARD=BRIO,DEV=0`). Only used for voice messages and the video link, both off unless switched on at the Mac.
- **Sound out:** Raspberry Pi OS Lite has no sound server, and the first sound card is often the webcam, which cannot play. Pair the panel's HDMI with the webcam's microphone once:
  ```bash
  sh ~/photobox/deploy/pi/set-audio.sh vc4hdmi0 BRIO
  sh ~/photobox/deploy/pi/set-audio.sh --test
  ```
  The second command plays a short tone through the panel. Card names are listed in `/proc/asound/cards`. The booth's sounds (ADR-047) and the video link play through this.
- **Printer:** plug it in and switch it on; it appears as `/dev/usb/lp0`. Printing itself is switched on at the Mac (`PHOTOBOX_PRINTING`), only once the notices say so (ADR-035).

## The booth's settings

All of the booth's own settings are in `~/.photobox-kiosk.env` on the Pi, one `KEY=value` per line. The setup menu (Ctrl+Alt+S on the start screen, or M in the first 10 seconds after a restart) writes most of them; the others you add over SSH. After changing the file by hand, restart the kiosk:
```bash
sudo systemctl restart photobox-kiosk
```

| Setting | Default | newbox | Set in the menu | Meaning |
|---|---|---|---|---|
| `PHOTOBOX_SERVER` | – | `http://192.168.1.27:8880` | 2 Server address | The Mac's address |
| `PHOTOBOX_CAMERA` | `0` | `0` | 4 Camera | The webcam |
| `PHOTOBOX_MICROPHONE` | – | `plughw:CARD=BRIO,DEV=0` | 7 Microphone | The microphone for voice messages |
| `PHOTOBOX_BUTTON` | – (none) | `25` | – | GPIO number of the push button |
| `PHOTOBOX_LIGHTS` | – (none) | `18,23,24` | – | GPIO numbers of red, green and blue |
| `PHOTOBOX_PERSON_DETECTION` | `false` | `true` | 8 Person detection | Notice whether someone is in front of the booth |
| `PHOTOBOX_PERSON_SENSOR` | `camera` | `tof` | 8 Person detection | `tof` uses the distance sensor instead of the camera |
| `PHOTOBOX_TOF_RANGE_MM` | `1200` | `1200` | 8, after "On, distance sensor" | Someone counts as there closer than this (100 to 2000) |
| `PHOTOBOX_PERSON_AREA` | (whole picture) | – | 8 Person detection | With the camera only: the part of the picture it looks at |
| `PHOTOBOX_IDLE_MINUTES` | `5` | `5` | – | Rest after this many minutes without a press; `0` never |
| `PHOTOBOX_IDLE_URL` | `https://www.midap.space` | – | – | The website the resting screen's QR code opens |
| `PHOTOBOX_SOUNDS` | `true` | `true` | 9 Sounds | The booth's soft sounds |
| `PHOTOBOX_SOUND_VOLUME` | `60` | `60` | 9 Sounds | 0 to 100 |

The pairing token is kept separately in `~/.photobox.token` and the setup menu's PIN in `~/.photobox-setup.json`; neither belongs in the settings file.

To switch on the button and lights as wired above:
```bash
printf 'PHOTOBOX_BUTTON=25\nPHOTOBOX_LIGHTS=18,23,24\n' >> ~/.photobox-kiosk.env && sudo systemctl restart photobox-kiosk
```
To switch the distance sensor on, use the setup menu (**8 Person detection**, **On, distance sensor**, then choose the range while watching the live distance), or:
```bash
printf 'PHOTOBOX_PERSON_DETECTION=true\nPHOTOBOX_PERSON_SENSOR=tof\nPHOTOBOX_TOF_RANGE_MM=1200\n' >> ~/.photobox-kiosk.env && sudo systemctl restart photobox-kiosk
```
If a key already appears in the file, edit that line instead of adding a second one (`nano ~/.photobox-kiosk.env`).

## Checking each part

Stop the kiosk first, so the checks and the kiosk do not use the same pins at once, and start it again afterwards:
```bash
sudo systemctl stop photobox-kiosk
```

| Part | Command | You should see |
|---|---|---|
| Button | `pinctrl set 25 ip pu` then `pinctrl get 25`, once released and once held | `hi` released, `lo` while held |
| Red light | `pinctrl set 18 op dh`, then `pinctrl set 18 op dl`, then `pinctrl set 18 ip` | red on, off, and the pin back to an input; the same with 23 (green) and 24 (blue) |
| Distance sensor | `~/photobox/.venv/bin/python -m photobox_client.tof test` | distances in mm, and "someone there" half a second after you step close (Ctrl+C to stop) |
| Sound | `sh ~/photobox/deploy/pi/set-audio.sh --test` | a short tone from the panel |
| Printer | `ls -l /dev/usb/lp*` | `/dev/usb/lp0`, group `lp` |
| Everything with the Mac | setup menu, **5 Test everything** | every line starts with OK |

```bash
sudo systemctl start photobox-kiosk
```

The kiosk then sets the pins up again itself. If the distance sensor check says the device is busy or gives odd readings, the kiosk is still running.
