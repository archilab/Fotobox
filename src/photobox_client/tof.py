"""Notices whether someone stands in front of the box with a time-of-flight distance sensor (ADR-046).

An ST VL53L0X on the Pi's I2C bus (bus 1, address 0x29) measures one distance in millimetres; it sees no
picture. Someone counts as there once the distance has stayed under the range for ON_SECONDS, and as gone
after OFF_SECONDS beyond it, so a hand or someone walking past does not flicker. It replaces the camera's
person detection (ADR-029) when the box is set to `PHOTOBOX_PERSON_SENSOR=tof`, with the same interface:
it runs between `resume` and `pause` and reports only yes or no. A failing sensor only means no signal.

    python -m photobox_client.tof test     # prints distances, for setting the range; nothing is kept

The start-up sequence follows Pololu's VL53L0X library for Arduino (MIT licence, Copyright (c) 2017 Pololu
Corporation), which is based on ST's VL53L0X API: the registers and values below are taken from it.
"""
from __future__ import annotations

import argparse
import threading
import time
from collections.abc import Callable
from typing import Protocol

BUS, ADDRESS = 1, 0x29
RANGE_MM = 1200  # someone stands closer than this; set per box with PHOTOBOX_TOF_RANGE_MM
READS_PER_SECOND = 5
ON_SECONDS = 0.5
OFF_SECONDS = 3.0
RETRY_SECONDS = 10
NOTHING_MM = 8190  # what the sensor reports when nothing is in range
TIMEOUT_SECONDS = 0.5

# Registers (from ST's API, as named in Pololu's library)
SYSRANGE_START = 0x00
SYSTEM_SEQUENCE_CONFIG = 0x01
SYSTEM_INTERRUPT_CONFIG_GPIO = 0x0A
SYSTEM_INTERRUPT_CLEAR = 0x0B
RESULT_INTERRUPT_STATUS = 0x13
RESULT_RANGE_STATUS = 0x14
FINAL_RANGE_CONFIG_MIN_COUNT_RATE_RTN_LIMIT = 0x44
MSRC_CONFIG_CONTROL = 0x60
GLOBAL_CONFIG_SPAD_ENABLES_REF_0 = 0xB0
GLOBAL_CONFIG_REF_EN_START_SELECT = 0xB6
DYNAMIC_SPAD_NUM_REQUESTED_REF_SPAD = 0x4E
DYNAMIC_SPAD_REF_EN_START_OFFSET = 0x4F
GPIO_HV_MUX_ACTIVE_HIGH = 0x84
VHV_CONFIG_PAD_SCL_SDA__EXTSUP_HV = 0x89
IDENTIFICATION_MODEL_ID = 0xC0

# ST's default tuning settings, written once at start-up (register, value).
TUNING = (
    (0xFF, 0x01), (0x00, 0x00), (0xFF, 0x00), (0x09, 0x00), (0x10, 0x00), (0x11, 0x00), (0x24, 0x01), (0x25, 0xFF),
    (0x75, 0x00), (0xFF, 0x01), (0x4E, 0x2C), (0x48, 0x00), (0x30, 0x20), (0xFF, 0x00), (0x30, 0x09), (0x54, 0x00),
    (0x31, 0x04), (0x32, 0x03), (0x40, 0x83), (0x46, 0x25), (0x60, 0x00), (0x27, 0x00), (0x50, 0x06), (0x51, 0x00),
    (0x52, 0x96), (0x56, 0x08), (0x57, 0x30), (0x61, 0x00), (0x62, 0x00), (0x64, 0x00), (0x65, 0x00), (0x66, 0xA0),
    (0xFF, 0x01), (0x22, 0x32), (0x47, 0x14), (0x49, 0xFF), (0x4A, 0x00), (0xFF, 0x00), (0x7A, 0x0A), (0x7B, 0x00),
    (0x78, 0x21), (0xFF, 0x01), (0x23, 0x34), (0x42, 0x00), (0x44, 0xFF), (0x45, 0x26), (0x46, 0x05), (0x40, 0x40),
    (0x0E, 0x06), (0x20, 0x1A), (0x43, 0x40), (0xFF, 0x00), (0x34, 0x03), (0x35, 0x44), (0xFF, 0x01), (0x31, 0x04),
    (0x4B, 0x09), (0x4C, 0x05), (0x4D, 0x04), (0xFF, 0x00), (0x44, 0x00), (0x45, 0x20), (0x47, 0x08), (0x48, 0x28),
    (0x67, 0x00), (0x70, 0x04), (0x71, 0x01), (0x72, 0xFE), (0x76, 0x00), (0x77, 0x00), (0xFF, 0x01), (0x0D, 0x01),
    (0xFF, 0x00), (0x80, 0x01), (0x01, 0xF8), (0xFF, 0x01), (0x8E, 0x01), (0x00, 0x01), (0xFF, 0x00), (0x80, 0x00),
)


class SensorError(Exception):
    pass


class Bus(Protocol):
    def read(self, register: int) -> int: ...
    def write(self, register: int, value: int) -> None: ...
    def read_block(self, register: int, length: int) -> list[int]: ...
    def write_block(self, register: int, values: list[int]) -> None: ...
    def close(self) -> None: ...


class SMBus:
    """The Pi's I2C bus through smbus2 (the `client-gpio` extra)."""

    def __init__(self, bus: int = BUS, address: int = ADDRESS) -> None:
        from smbus2 import SMBus as Raw
        self.raw, self.address = Raw(bus), address

    def read(self, register: int) -> int:
        return self.raw.read_byte_data(self.address, register)

    def write(self, register: int, value: int) -> None:
        self.raw.write_byte_data(self.address, register, value & 0xFF)

    def read_block(self, register: int, length: int) -> list[int]:
        return list(self.raw.read_i2c_block_data(self.address, register, length))

    def write_block(self, register: int, values: list[int]) -> None:
        self.raw.write_i2c_block_data(self.address, register, list(values))

    def close(self) -> None:
        self.raw.close()


class VL53L0X:
    """Single measurements on request, after ST's start-up and reference calibration."""

    def __init__(self, bus: Bus, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.bus, self.sleep, self.clock = bus, sleep, clock
        self.stop_variable = 0

    def _wait(self, done: Callable[[], bool]) -> None:
        deadline = self.clock() + TIMEOUT_SECONDS
        while not done():
            if self.clock() > deadline:
                raise SensorError("the sensor did not answer in time")
            self.sleep(0.002)

    def _writes(self, pairs) -> None:
        for register, value in pairs:
            self.bus.write(register, value)

    def start(self) -> "VL53L0X":
        bus = self.bus
        if bus.read(IDENTIFICATION_MODEL_ID) != 0xEE:
            raise SensorError("no VL53L0X at this address")
        bus.write(VHV_CONFIG_PAD_SCL_SDA__EXTSUP_HV, bus.read(VHV_CONFIG_PAD_SCL_SDA__EXTSUP_HV) | 0x01)  # 2.8 V I/O
        self._writes(((0x88, 0x00), (0x80, 0x01), (0xFF, 0x01), (0x00, 0x00)))
        self.stop_variable = bus.read(0x91)
        self._writes(((0x00, 0x01), (0xFF, 0x00), (0x80, 0x00)))
        bus.write(MSRC_CONFIG_CONTROL, bus.read(MSRC_CONFIG_CONTROL) | 0x12)  # no MSRC and pre-range limit checks
        limit = int(0.25 * (1 << 7))  # final range signal rate limit, 0.25 MCPS in 9.7 fixed point
        bus.write_block(FINAL_RANGE_CONFIG_MIN_COUNT_RATE_RTN_LIMIT, [(limit >> 8) & 0xFF, limit & 0xFF])
        bus.write(SYSTEM_SEQUENCE_CONFIG, 0xFF)
        count, aperture = self._spad_info()
        spads = bus.read_block(GLOBAL_CONFIG_SPAD_ENABLES_REF_0, 6)
        self._writes(((0xFF, 0x01), (DYNAMIC_SPAD_REF_EN_START_OFFSET, 0x00), (DYNAMIC_SPAD_NUM_REQUESTED_REF_SPAD, 0x2C),
                      (0xFF, 0x00), (GLOBAL_CONFIG_REF_EN_START_SELECT, 0xB4)))
        first, enabled = (12 if aperture else 0), 0
        for i in range(48):
            if i < first or enabled == count:
                spads[i // 8] &= ~(1 << (i % 8)) & 0xFF
            elif (spads[i // 8] >> (i % 8)) & 0x1:
                enabled += 1
        bus.write_block(GLOBAL_CONFIG_SPAD_ENABLES_REF_0, spads)
        self._writes(TUNING)
        bus.write(SYSTEM_INTERRUPT_CONFIG_GPIO, 0x04)  # a new sample is ready
        bus.write(GPIO_HV_MUX_ACTIVE_HIGH, bus.read(GPIO_HV_MUX_ACTIVE_HIGH) & ~0x10 & 0xFF)
        bus.write(SYSTEM_INTERRUPT_CLEAR, 0x01)
        budget = self._timing_budget()
        bus.write(SYSTEM_SEQUENCE_CONFIG, 0xE8)
        self._set_timing_budget(budget)  # the final range gets the time the sequence no longer uses
        bus.write(SYSTEM_SEQUENCE_CONFIG, 0x01)
        self._calibrate(0x40)  # VHV
        bus.write(SYSTEM_SEQUENCE_CONFIG, 0x02)
        self._calibrate(0x00)  # phase
        bus.write(SYSTEM_SEQUENCE_CONFIG, 0xE8)
        return self

    # Timing (ST's API via Pololu): timeouts in macro periods, which depend on the VCSEL pulse period.

    def _read16(self, register: int) -> int:
        high, low = self.bus.read_block(register, 2)
        return (high << 8) | low

    @staticmethod
    def _macro_period_ns(vcsel_pclks: int) -> int:
        return (2304 * vcsel_pclks * 1655 + 500) // 1000

    @classmethod
    def _mclks_to_us(cls, mclks: int, vcsel_pclks: int) -> int:
        return (mclks * cls._macro_period_ns(vcsel_pclks) + 500) // 1000

    @classmethod
    def _us_to_mclks(cls, us: int, vcsel_pclks: int) -> int:
        period = cls._macro_period_ns(vcsel_pclks)
        return (us * 1000 + period // 2) // period

    @staticmethod
    def _decode_timeout(value: int) -> int:
        return ((value & 0xFF) << ((value & 0xFF00) >> 8)) + 1

    @staticmethod
    def _encode_timeout(mclks: int) -> int:
        if mclks <= 0:
            return 0
        low, high = mclks - 1, 0
        while low & 0xFFFFFF00:
            low >>= 1
            high += 1
        return (high << 8) | (low & 0xFF)

    def _steps(self) -> dict:
        config = self.bus.read(SYSTEM_SEQUENCE_CONFIG)
        enabled = {"tcc": (config >> 4) & 1, "dss": (config >> 3) & 1, "msrc": (config >> 2) & 1,
                   "pre_range": (config >> 6) & 1, "final_range": (config >> 7) & 1}
        pre_vcsel = (self.bus.read(0x50) + 1) << 1    # PRE_RANGE_CONFIG_VCSEL_PERIOD
        final_vcsel = (self.bus.read(0x70) + 1) << 1  # FINAL_RANGE_CONFIG_VCSEL_PERIOD
        msrc_mclks = self.bus.read(0x46) + 1          # MSRC_CONFIG_TIMEOUT_MACROP
        pre_mclks = self._decode_timeout(self._read16(0x51))    # PRE_RANGE_CONFIG_TIMEOUT_MACROP_HI
        final_mclks = self._decode_timeout(self._read16(0x71))  # FINAL_RANGE_CONFIG_TIMEOUT_MACROP_HI
        if enabled["pre_range"]:
            final_mclks -= pre_mclks
        return enabled | {"msrc_us": self._mclks_to_us(msrc_mclks, pre_vcsel), "pre_us": self._mclks_to_us(pre_mclks, pre_vcsel),
                          "pre_mclks": pre_mclks, "final_us": self._mclks_to_us(final_mclks, final_vcsel), "final_vcsel": final_vcsel}

    def _used_us(self, steps: dict) -> int:
        used = 1910 + 960  # start and end overhead
        if steps["tcc"]:
            used += steps["msrc_us"] + 590
        if steps["dss"]:
            used += 2 * (steps["msrc_us"] + 690)
        elif steps["msrc"]:
            used += steps["msrc_us"] + 660
        if steps["pre_range"]:
            used += steps["pre_us"] + 660
        return used

    def _timing_budget(self) -> int:
        steps = self._steps()
        return self._used_us(steps) + (steps["final_us"] + 550 if steps["final_range"] else 0)

    def _set_timing_budget(self, budget_us: int) -> None:
        steps = self._steps()
        if not steps["final_range"]:
            return
        used = self._used_us(steps) + 550
        if used > budget_us:
            raise SensorError("timing budget too small")
        mclks = self._us_to_mclks(budget_us - used, steps["final_vcsel"])
        if steps["pre_range"]:
            mclks += steps["pre_mclks"]
        value = self._encode_timeout(mclks)
        self.bus.write_block(0x71, [(value >> 8) & 0xFF, value & 0xFF])

    def _spad_info(self) -> tuple[int, bool]:
        bus = self.bus
        self._writes(((0x80, 0x01), (0xFF, 0x01), (0x00, 0x00), (0xFF, 0x06)))
        bus.write(0x83, bus.read(0x83) | 0x04)
        self._writes(((0xFF, 0x07), (0x81, 0x01), (0x80, 0x01), (0x94, 0x6B), (0x83, 0x00)))
        self._wait(lambda: bus.read(0x83) != 0x00)
        bus.write(0x83, 0x01)
        value = bus.read(0x92)
        self._writes(((0x81, 0x00), (0xFF, 0x06)))
        bus.write(0x83, bus.read(0x83) & ~0x04 & 0xFF)
        self._writes(((0xFF, 0x01), (0x00, 0x01), (0xFF, 0x00), (0x80, 0x00)))
        return value & 0x7F, bool((value >> 7) & 0x01)

    def _calibrate(self, vhv_init: int) -> None:
        self.bus.write(SYSRANGE_START, 0x01 | vhv_init)
        self._wait(lambda: self.bus.read(RESULT_INTERRUPT_STATUS) & 0x07 != 0)
        self.bus.write(SYSTEM_INTERRUPT_CLEAR, 0x01)
        self.bus.write(SYSRANGE_START, 0x00)

    def measure(self) -> int:
        """One distance in millimetres; NOTHING_MM or more when nothing is in range."""
        bus = self.bus
        self._writes(((0x80, 0x01), (0xFF, 0x01), (0x00, 0x00), (0x91, self.stop_variable), (0x00, 0x01), (0xFF, 0x00), (0x80, 0x00)))
        bus.write(SYSRANGE_START, 0x01)
        self._wait(lambda: bus.read(SYSRANGE_START) & 0x01 == 0)
        self._wait(lambda: bus.read(RESULT_INTERRUPT_STATUS) & 0x07 != 0)
        high, low = bus.read_block(RESULT_RANGE_STATUS + 10, 2)
        status = (bus.read(RESULT_RANGE_STATUS) >> 3) & 0x0F
        bus.write(SYSTEM_INTERRUPT_CLEAR, 0x01)
        distance = (high << 8) | low
        return distance if status == 11 else max(distance, NOTHING_MM)  # 11: a valid range

    def close(self) -> None:
        self.bus.close()


class Smoother:
    """Someone is there after ON_SECONDS within range, and gone after OFF_SECONDS beyond it."""

    def __init__(self, range_mm: int) -> None:
        self.range_mm = range_mm
        self.present = False
        self.since: float | None = None

    def update(self, distance_mm: int, now: float) -> bool:
        near = distance_mm < self.range_mm
        if near == self.present:
            self.since = None
        elif self.since is None:
            self.since = now
        elif now - self.since >= (ON_SECONDS if near else OFF_SECONDS):
            self.present, self.since = near, None
        return self.present


class TofDetector:
    """The same interface as the camera's PersonDetector, without a camera (ADR-046)."""

    uses_camera = False  # the start screen shows no camera note

    def __init__(self, open_sensor: Callable[[], VL53L0X], on_change: Callable[[bool], None], *, range_mm: int = RANGE_MM,
                 rate: int = READS_PER_SECOND, clock: Callable[[], float] = time.monotonic, retry_seconds: float = RETRY_SECONDS) -> None:
        self.open_sensor, self.on_change, self.range_mm = open_sensor, on_change, range_mm
        self.interval, self.clock, self.retry_seconds = 1 / rate, clock, retry_seconds
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.present = False

    def resume(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop.clear()
        self.thread = threading.Thread(target=self._run, name="photobox-tof", daemon=True)
        self.thread.start()

    def pause(self) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        self._tell(False)

    def _tell(self, present: bool) -> None:
        if present != self.present:
            self.present = present
            try:
                self.on_change(present)
            except Exception:
                pass

    def _run(self) -> None:
        while not self.stop.is_set():
            try:
                self._watch()
            except Exception:
                self._tell(False)  # no signal, not a false one
            self.stop.wait(self.retry_seconds)

    def _watch(self) -> None:
        sensor = self.open_sensor()
        smoother = Smoother(self.range_mm)
        try:
            while not self.stop.is_set():
                started = self.clock()
                self._tell(smoother.update(sensor.measure(), self.clock()))
                self.stop.wait(max(0.0, self.interval - (self.clock() - started)))
        finally:
            sensor.close()


def open_vl53l0x() -> VL53L0X:
    bus = SMBus()
    try:
        return VL53L0X(bus).start()
    except Exception:
        bus.close()
        raise


def bus_present() -> bool:
    """Whether the Pi's I2C bus is switched on (raspi-config, Interface Options, I2C)."""
    from pathlib import Path
    return Path(f"/dev/i2c-{BUS}").exists()


def create_tof_detector(range_mm: int, on_change: Callable[[bool], None]) -> TofDetector | None:
    """A detector on the VL53L0X, or None without the I2C bus or smbus2."""
    try:
        import smbus2  # noqa: F401
    except ImportError:
        return None
    if not bus_present():
        return None
    return TofDetector(open_vl53l0x, on_change, range_mm=range_mm)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Distance sensor test: prints distances, nothing is kept")
    parser.add_argument("command", choices=["test"])
    parser.add_argument("--range", type=int, default=RANGE_MM, help="the range to compare with, in millimetres")
    parser.add_argument("--seconds", type=float, default=0, help="stop after this long (default: Ctrl+C)")
    args = parser.parse_args(argv)
    sensor = open_vl53l0x()
    smoother = Smoother(args.range)
    started = time.monotonic()
    try:
        while not args.seconds or time.monotonic() - started < args.seconds:
            distance = sensor.measure()
            present = smoother.update(distance, time.monotonic())
            shown = "nothing" if distance >= NOTHING_MM else f"{distance} mm"
            print(f"{shown:>10}   {'someone there' if present else '-'}", flush=True)
            time.sleep(1 / READS_PER_SECOND)
    except KeyboardInterrupt:
        pass
    finally:
        sensor.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
