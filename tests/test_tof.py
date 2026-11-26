"""The distance sensor (ADR-046), with a fake I2C bus: no sensor or smbus2 is needed."""
from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace

import pytest

from photobox_client import tof
from photobox_client.kiosk import Kiosk
from photobox_client.settings import Settings


class FakeBus:
    """Registers that read back what was written, except those the sensor itself changes."""

    def __init__(self, model_id: int = 0xEE, distance: int = 640, status: int = 11) -> None:
        self.registers: dict[int, int] = {tof.IDENTIFICATION_MODEL_ID: model_id, 0x92: 0x85}  # SPAD info: 5 aperture SPADs
        self.distance, self.status = distance, status
        self.writes: list[tuple[int, int]] = []
        self.closed = False

    def read(self, register: int) -> int:
        if register == tof.SYSRANGE_START:
            return 0  # a measurement is done at once
        if register == 0x83:
            return 0x01  # SPAD info ready
        if register == tof.RESULT_INTERRUPT_STATUS:
            return 0x07  # a new measurement
        if register == tof.RESULT_RANGE_STATUS:
            return self.status << 3
        return self.registers.get(register, 0)

    def write(self, register: int, value: int) -> None:
        self.writes.append((register, value))
        self.registers[register] = value

    def read_block(self, register: int, length: int) -> list[int]:
        if register == tof.RESULT_RANGE_STATUS + 10:
            return [self.distance >> 8, self.distance & 0xFF]
        return [self.registers.get(register + i, 0) for i in range(length)]

    def write_block(self, register: int, values: list[int]) -> None:
        for i, value in enumerate(values):
            self.write(register + i, value)

    def close(self) -> None:
        self.closed = True


def sensor(bus: FakeBus) -> tof.VL53L0X:
    return tof.VL53L0X(bus, sleep=lambda _s: None).start()


def test_start_writes_the_tuning_settings_in_order():
    bus = FakeBus()
    sensor(bus)
    writes = bus.writes
    start = next(i for i in range(len(writes)) if tuple(writes[i:i + len(tof.TUNING)]) == tof.TUNING)
    assert start > 0
    assert (tof.VHV_CONFIG_PAD_SCL_SDA__EXTSUP_HV, 0x01) in writes  # 2.8 V I/O


def test_start_refuses_another_chip():
    with pytest.raises(tof.SensorError):
        sensor(FakeBus(model_id=0x00))


def test_measure_gives_a_valid_distance():
    bus = FakeBus(distance=640)
    assert sensor(bus).measure() == 640
    assert (tof.SYSTEM_INTERRUPT_CLEAR, 0x01) in bus.writes


@pytest.mark.parametrize("status", [4, 5, 0])
def test_measure_without_a_valid_range_is_nothing(status):
    assert sensor(FakeBus(distance=30, status=status)).measure() >= tof.NOTHING_MM


def test_a_sensor_that_does_not_finish_times_out():
    bus = FakeBus()
    sensor_ = sensor(bus)
    bus.read = lambda register: 1 if register == tof.SYSRANGE_START else 0  # never finishes
    ticks = iter(range(100))
    sensor_.clock = lambda: next(ticks) * 0.1
    with pytest.raises(tof.SensorError):
        sensor_.measure()


def test_someone_counts_only_after_staying_and_leaves_only_after_a_while():
    smoother = tof.Smoother(1000)
    assert smoother.update(500, 0.0) is False
    assert smoother.update(500, 0.3) is False
    assert smoother.update(500, tof.ON_SECONDS) is True
    assert smoother.update(tof.NOTHING_MM, 1.0) is True  # a moment out of range is not leaving
    assert smoother.update(500, 1.2) is True
    assert smoother.update(tof.NOTHING_MM, 2.0) is True
    assert smoother.update(tof.NOTHING_MM, 2.0 + tof.OFF_SECONDS) is False


def test_a_hand_passing_is_not_someone():
    smoother = tof.Smoother(1000)
    assert smoother.update(300, 0.0) is False
    assert smoother.update(tof.NOTHING_MM, 0.2) is False
    assert smoother.update(300, 0.4) is False


class FakeSensor:
    def __init__(self, distance: int) -> None:
        self.distance, self.closed = distance, False

    def measure(self) -> int:
        return self.distance

    def close(self) -> None:
        self.closed = True


def wait_for(condition, seconds: float = 3.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_detector_reports_someone_and_no_one_after_pause():
    reports: list[bool] = []
    fake = FakeSensor(400)
    detector = tof.TofDetector(lambda: fake, reports.append, range_mm=1000, rate=50)
    assert detector.uses_camera is False
    detector.resume()
    wait_for(lambda: reports == [True])
    detector.pause()
    assert reports == [True, False]
    assert fake.closed


def test_detector_without_a_sensor_reports_no_one_and_keeps_trying():
    reports: list[bool] = []
    tries = threading.Event()

    def broken():
        tries.set()
        raise OSError("no device at 0x29")

    detector = tof.TofDetector(broken, reports.append, rate=50, retry_seconds=0.01)
    detector.resume()
    wait_for(tries.is_set)
    detector.pause()
    assert reports == []  # never said someone was there


def test_no_detector_without_smbus2(monkeypatch):
    monkeypatch.setitem(sys.modules, "smbus2", None)
    assert tof.create_tof_detector(1000, lambda _present: None) is None


def test_settings_keep_the_sensor_and_range(tmp_path):
    path = tmp_path / "settings.env"
    Settings(person_detection=True, person_sensor="tof", tof_range_mm=900).save(path)
    loaded = Settings.load(path)
    assert (loaded.person_detection, loaded.person_sensor, loaded.tof_range_mm) == (True, "tof", 900)


@pytest.mark.parametrize("text, sensor_, range_mm", [("PHOTOBOX_PERSON_SENSOR=lidar\nPHOTOBOX_TOF_RANGE_MM=5", "camera", 1200),
                                                     ("PHOTOBOX_PERSON_SENSOR=TOF\nPHOTOBOX_TOF_RANGE_MM=800", "tof", 800),
                                                     ("", "camera", 1200)])
def test_settings_fall_back_to_the_camera_and_default_range(tmp_path, text, sensor_, range_mm):
    path = tmp_path / "settings.env"
    path.write_text(text)
    loaded = Settings.load(path)
    assert (loaded.person_sensor, loaded.tof_range_mm) == (sensor_, range_mm)


def test_start_screen_mentions_the_camera_only_when_the_camera_looks():
    assert Kiosk._camera_looks(SimpleNamespace(detector=None)) is False
    assert Kiosk._camera_looks(SimpleNamespace(detector=SimpleNamespace())) is True  # the camera's detector
    assert Kiosk._camera_looks(SimpleNamespace(detector=SimpleNamespace(uses_camera=False))) is False
