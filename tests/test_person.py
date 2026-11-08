"""The person detector on the box (ADR-029, milestone D2). No real camera or model; made-up frames only."""
import inspect
import re
import threading
import time
import numpy as np
import pytest
import photobox_client.person as person
from photobox_client.kiosk import ENTER, Kiosk
from photobox_client.person import Area, PersonDetector, Smoother, create_detector
from photobox_client.settings import Settings


def feed(smoother, scores, step=0.33, start=0.0):
    now, result = start, False
    for score in scores:
        result = smoother.update(score, now)
        now += step
    return result, now


# Steady answers from unsteady scores

def test_says_yes_after_a_steady_run():
    assert feed(Smoother(), [0.8] * 3)[0] is True


def test_one_frame_never_says_yes():
    assert feed(Smoother(), [0.2, 0.9, 0.2, 0.2, 0.2])[0] is False


def test_a_few_stray_frames_at_the_threshold_do_not_say_yes():
    assert feed(Smoother(), [0.2, 0.55, 0.3, 0.2, 0.55, 0.3, 0.2, 0.3])[0] is False


def test_empty_booth_scores_stay_no():
    assert feed(Smoother(), [0.41, 0.39, 0.44, 0.40] * 20)[0] is False


def test_person_with_uneven_scores_stays_yes():
    assert feed(Smoother(), [0.58, 0.45, 0.6, 0.55] * 20)[0] is True


def test_a_short_dropout_does_not_say_no():
    smoother = Smoother()
    _, now = feed(smoother, [0.8] * 6)
    assert feed(smoother, [0.1] * 9, start=now)[0] is True  # 3 s


def test_says_no_after_the_person_has_been_gone_a_while():
    smoother = Smoother()
    _, now = feed(smoother, [0.8] * 6)
    assert feed(smoother, [0.1] * 25, start=now)[0] is False  # about 8 s


# The area

def test_area_crops_to_the_inside_of_the_box():
    frame = np.zeros((100, 200, 3), np.uint8)
    assert Area(0.25, 0.5, 0.75, 1.0).crop(frame).shape == (50, 100, 3)


@pytest.mark.parametrize("text", ["", "nonsense", "0,0,1", "0.5,0,0.2,1", "-1,0,1,1", "0,0,2,1"])
def test_bad_areas_mean_the_whole_picture(text):
    assert Area.parse(text) == Area()


def test_area_is_read_from_its_setting():
    assert Area.parse("0.1,0.2,0.9,1") == Area(0.1, 0.2, 0.9, 1.0)


# The detector

class Camera:
    def __init__(self, opened):
        self.opened, self.released = opened, False

    def read(self):
        return True, np.zeros((360, 640, 3), np.uint8)

    def release(self):
        self.released = True


class Model:
    def __init__(self, score=0.9):
        self.value, self.seen = score, []

    def score(self, frame):
        self.seen.append(frame.shape)
        return self.value


def detector(model=None, changes=None, opened=None, fail_open=False, **options):
    opened = [] if opened is None else opened

    def open_camera():
        if fail_open:
            raise RuntimeError("busy")
        camera = Camera(opened)
        opened.append(camera)
        return camera

    changes = [] if changes is None else changes
    return PersonDetector(open_camera, lambda: model or Model(), changes.append, fps=100, retry_seconds=0.01, **options), changes, opened


def wait_until(check, seconds=5):
    deadline = time.monotonic() + seconds
    while not check() and time.monotonic() < deadline:
        time.sleep(0.01)
    return check()


def test_reports_a_person_and_releases_the_camera_on_pause():
    d, changes, opened = detector()
    d.resume()
    # A real clock: three frames at 100 per second is well within two seconds.
    assert wait_until(lambda: changes == [True])
    d.pause()
    assert opened[0].released and not d.thread.is_alive()
    assert changes == [True, False]  # a visit begins: LIVE takes over


def test_nothing_is_opened_before_resume():
    _, changes, opened = detector()
    assert opened == [] and changes == []


def test_area_is_applied_before_the_model_sees_the_frame():
    model = Model()
    d, changes, _ = detector(model=model, area=Area(0, 0, 0.5, 0.5))
    d.resume()
    wait_until(lambda: changes)
    d.pause()
    assert model.seen and set(model.seen) == {(180, 320, 3)}


def test_a_broken_camera_gives_no_signal_and_no_crash():
    d, changes, _ = detector(fail_open=True)
    d.resume()
    time.sleep(0.05)
    assert d.thread.is_alive() and changes == []
    d.pause()
    assert not d.thread.is_alive()


def test_a_broken_model_gives_no_signal():
    opened = []
    d = PersonDetector(lambda: opened.append(Camera(opened)) or opened[-1], lambda: (_ for _ in ()).throw(RuntimeError("no model")), lambda _p: pytest.fail("no signal"), retry_seconds=0.01)
    d.resume()
    time.sleep(0.05)
    d.pause()


def test_a_failing_report_does_not_stop_it():
    def fails(_present):
        raise RuntimeError("network")
    d = PersonDetector(lambda: Camera([]), lambda: Model(), fails, fps=100, retry_seconds=0.01)
    d.resume()
    assert wait_until(lambda: d.present)
    d.pause()


def test_no_detector_without_its_model(tmp_path):
    assert create_detector("0", "", lambda _p: None, model_path=tmp_path / "missing.onnx") is None


# The rules of ADR-029

def test_off_by_default_and_only_on_when_set(tmp_path):
    assert Settings().person_detection is False
    path = tmp_path / "kiosk.env"
    path.write_text("PHOTOBOX_SERVER=http://mac:8000\n")
    assert Settings.load(path).person_detection is False
    Settings(server="http://mac:8000", person_detection=True, person_area="0,0,1,1").save(path)
    loaded = Settings.load(path)
    assert loaded.person_detection is True and loaded.person_area == "0,0,1,1"


def test_the_detector_cannot_save_or_send_a_picture():
    source = inspect.getsource(person)
    assert not re.search(r"imwrite|VideoWriter|imencode|\.save\(|np\.save|httpx|requests|socket|urllib|logging", source)
    assert not re.search(r"face|embedding|identif(?!ied)|gender|\bage\b|track", source.split('"""', 2)[2].lower())


class Recording:
    def __init__(self, log):
        self.log = log

    def resume(self):
        self.log.append("resume")

    def pause(self):
        self.log.append("pause")


class Keys:
    def __init__(self, *answers):
        self.answers = list(answers)

    def flush(self):
        pass

    def wait(self, accepted, timeout):
        return self.answers.pop(0)

    def read_key(self, timeout):
        return None


class Screen:
    def show(self, text):
        pass


class Api:
    def __init__(self, log):
        self.log = log

    def presence(self):
        return None

    def notice(self):
        self.log.append("notice")
        return "v", "NOTICE", None

    def start(self, version, pool=None):
        self.log.append("consent")
        return "sid", "CODE"

    def upload_and_process(self, sid, image):
        pass

    def status(self, sid):
        return {"status": "complete", "job": None}


def test_it_runs_only_on_the_start_screen_and_is_off_before_the_photo():
    log = []

    def camera():
        log.append("photo")
        return b"jpeg"

    kiosk = Kiosk(Api(log), camera, Screen(), Keys(ENTER, ENTER, ENTER), sleep=lambda _s: None, detector=Recording(log))
    assert kiosk.run_once() == "complete"
    assert log == ["resume", "pause", "notice", "consent", "photo"]


def test_it_is_released_even_if_the_start_screen_fails():
    log = []

    class Broken(Api):
        def presence(self):
            raise RuntimeError("unexpected")

    kiosk = Kiosk(Broken(log), lambda: b"", Screen(), Keys(ENTER), detector=Recording(log))
    with pytest.raises(RuntimeError):
        kiosk.start_screen()
    assert log == ["resume", "pause"]
