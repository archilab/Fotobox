"""Printing the strip on a receipt printer (ADR-035). Drawn images and files stand in for the printer."""
import io
import os
from pathlib import Path

import httpx
import pytest
from PIL import Image

from photobox_client.health import Counters, collect
from photobox_client.kiosk import ENTER, PRINTING, Kiosk
from photobox_client.printer import (
    FEED_AND_CUT,
    INITIALIZE,
    PAPER_DOTS,
    PrinterError,
    UsbPrinter,
    find_printer,
    paper_state,
    raster,
)
from photobox_server.dashboard import box_problems

ROOT = Path(__file__).parent.parent


def png(width=560, height=40, black_column=None):
    image = Image.new("1", (width, height), 1)
    if black_column is not None:
        for y in range(height):
            image.putpixel((black_column, y), 0)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def bands(data):
    """The raster commands in ESC/POS bytes, as (bytes per row, lines, dot data)."""
    assert data.startswith(INITIALIZE) and data.endswith(FEED_AND_CUT)
    body, found = data[len(INITIALIZE):-len(FEED_AND_CUT)], []
    while body:
        assert body[:4] == b"\x1dv0\x00"
        row, lines = int.from_bytes(body[4:6], "little"), int.from_bytes(body[6:8], "little")
        found.append((row, lines, body[8:8 + row * lines]))
        body = body[8 + row * lines:]
    return found


def test_a_strip_is_centred_on_80_mm_paper_in_bands():
    found = bands(raster(Image.open(io.BytesIO(png(560, 600)))))
    assert [lines for _, lines, _ in found] == [256, 256, 88]
    assert {row for row, _, _ in found} == {PAPER_DOTS // 8}


def test_black_dots_are_set_bits_in_the_right_place():
    (_, _, dots), = bands(raster(Image.open(io.BytesIO(png(560, 1, black_column=0)))))
    column = (PAPER_DOTS - 560) // 2  # centred
    assert dots[column // 8] == 0x80 >> (column % 8)
    assert sum(b.bit_count() for b in dots) == 1


def test_a_strip_wider_than_the_paper_is_scaled_down():
    (row, lines, _), = bands(raster(Image.open(io.BytesIO(png(1152, 100)))))
    assert row == PAPER_DOTS // 8 and lines == 50


@pytest.mark.parametrize("reply,state", [(b"\x12", "ready"), (b"\x72", "no paper"), (b"\x1e", "ready"), (b"", "unknown"), (b"\xff", "unknown")])
def test_paper_sensor_replies(reply, state):
    assert paper_state(reply) == state


def test_printing_writes_the_raster_to_the_device(tmp_path):
    device = tmp_path / "lp0"
    device.touch()
    UsbPrinter(device).print_strip(png(560, 10))
    assert device.read_bytes() == raster(Image.open(io.BytesIO(png(560, 10))))


def test_a_printer_that_cannot_be_opened_is_an_error(tmp_path):
    fifo = tmp_path / "lp0"
    os.mkfifo(fifo)  # nobody reads it, like a printer that is gone
    with pytest.raises(PrinterError):
        UsbPrinter(fifo).print_strip(png())


def test_an_unreadable_strip_is_an_error(tmp_path):
    with pytest.raises(PrinterError):
        UsbPrinter(tmp_path / "lp0").print_strip(b"not an image")


def test_only_a_writable_printer_is_found(tmp_path):
    (tmp_path / "lp0").touch(mode=0o400)
    (tmp_path / "lp1").touch()
    assert find_printer(str(tmp_path / "lp*")).path == tmp_path / "lp1"
    assert find_printer(str(tmp_path / "none*")) is None


class Screen:
    def __init__(self):
        self.shown = []

    def show(self, text):
        self.shown.append(text)


class Keys:
    def __init__(self, *answers):
        self.answers = list(answers)

    def flush(self):
        pass

    def wait(self, accepted, timeout):
        return self.answers.pop(0) if self.answers else None


class Api:
    pool = None

    def __init__(self, printing=True, strip_fails=False):
        self.printing, self.strip_fails, self.profiles = printing, strip_fails, []

    def presence(self):
        return None

    def notice(self):
        return "notice-abc", "NOTICE", None

    def start(self, version, pool=None):
        return "sid", "CODE-1"

    def upload_and_process(self, sid, image):
        pass

    def status(self, sid):
        return {"status": "complete", "job": None}

    def strip(self, sid, profile="color"):
        self.profiles.append(profile)
        if self.strip_fails:
            raise httpx.ConnectError("down")
        return png()


class Printer:
    def __init__(self, paper="ready", fails=False):
        self.paper, self.fails, self.printed = paper, fails, []

    def status(self):
        return self.paper

    def print_strip(self, data):
        if self.fails:
            raise PrinterError("jammed")
        self.printed.append(data)


def visit(api, printer):
    screen, results, shown = Screen(), [], []
    kiosk = Kiosk(api, lambda: b"jpeg", screen, Keys(ENTER, ENTER), sleep=lambda _s: None, clock=iter(range(10_000)).__next__,
                  printer=lambda: printer, on_print=results.append)
    kiosk.show_strip = lambda sid, code: shown.append(sid) or True
    assert kiosk.run_once() == "complete"
    return screen, results, shown


def test_a_strip_is_printed_once_instead_of_shown():
    api, printer = Api(), Printer()
    screen, results, shown = visit(api, printer)
    assert len(printer.printed) == 1 and api.profiles == ["thermal"]
    assert shown == [] and results == ["ready"]
    assert any(PRINTING in text for text in screen.shown)
    assert "CODE-1" in screen.shown[-1]


def test_nothing_is_printed_while_the_server_has_printing_off():
    api, printer = Api(printing=False), Printer()
    _, results, shown = visit(api, printer)
    assert printer.printed == [] and api.profiles == [] and shown == ["sid"] and results == []


def test_without_a_printer_the_strip_is_shown():
    _, results, shown = visit(Api(), None)
    assert shown == ["sid"] and results == []


@pytest.mark.parametrize("api,printer,result", [(Api(), Printer(paper="no paper"), "no paper"), (Api(), Printer(fails=True), "error"),
                                                (Api(strip_fails=True), Printer(), "error")])
def test_a_failed_print_shows_the_strip_instead(api, printer, result):
    _, results, shown = visit(api, printer)
    assert printer.printed == [] and shown == ["sid"] and results == [result]


def test_the_server_tells_the_box_whether_printing_is_on(client, monkeypatch, tmp_path):
    notice = tmp_path / "notice.txt"
    notice.write_text("A notice without placeholders.")
    monkeypatch.setenv("PHOTOBOX_CONSENT_NOTICE_FILE", str(notice))
    assert client.get("/api/v1/consent-notice").json()["printing"] is False
    monkeypatch.setenv("PHOTOBOX_PRINTING", "true")
    assert client.get("/api/v1/consent-notice").json()["printing"] is True


def test_printer_figures_reach_the_dashboard(client):
    counters = Counters()
    counters.printer, counters.print_failures = "no paper", 2
    health = collect(counters, runner=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert client.post("/api/v1/devices/status", json={"state": "idle", "health": health}).status_code == 204
    row = {"revoked_at": None, "reported_at": "2026-11-24T00:00:00+00:00", "state": "idle", "detail": None}
    problems = box_problems(row, True, health)
    assert "printer has no paper" in problems and any("printing failed 2 times" in p for p in problems)


def test_the_kiosk_service_may_use_the_printer():
    assert "SupplementaryGroups=lp" in (ROOT / "deploy" / "pi" / "photobox-kiosk.service").read_text()
