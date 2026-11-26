"""MIDAP's line on the booth screen (ADR-039): structure of the drawing code, and the data it is drawn from."""
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from photobox_client import views
from photobox_client.kiosk import ENTER, Kiosk

UI = Path(__file__).parent.parent / "src" / "photobox_client" / "ui"
SCREENS = (UI / "screens.js").read_text(encoding="utf-8")


def view_body(name):
    start = SCREENS.index(f"    {name}: (")
    rest = SCREENS[start + 10:]
    following = re.search(r"\n    [a-z_]+: \(", rest)
    return rest[: following.start()] if following else rest


def test_midaps_mark_appears_on_the_start_screen_only():
    assert "midap()" in view_body("start")
    for name in ("invite", "consent", "making", "live_moment", "done", "message", "strip"):
        assert "midap()" not in view_body(name), name


def test_no_funding_wording_is_invented():
    """The project has not given its funding notice yet; the screen shows nothing in its place."""
    text = SCREENS.lower()
    assert not any(word in text for word in ("gefördert", "funded by", "förderung", "funding"))


def test_the_mark_is_drawn_not_loaded():
    assert "createElementNS" in SCREENS and "MIDAP_MARK" in SCREENS
    assert not re.search(r"https?://(?!www\.w3\.org/2000/svg)", SCREENS)


def test_lines_respect_reduced_motion_and_never_replay_on_a_redraw():
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in SCREENS
    assert "drawLines(node, node.classList.contains(\"instant\"))" in SCREENS
    assert "drawn.get(spec.key)" in SCREENS  # a redraw continues from where the line was


def test_consent_and_countdown_carry_no_line():
    for name in ("consent", "countdown", "flash"):
        assert "Lines(" not in view_body(name), name


def test_each_place_draws_its_own_line_and_the_lines_have_no_colour():
    assert "seeded(place)" in SCREENS
    css = (UI / "ui.css").read_text(encoding="utf-8")
    rules = " ".join(line for line in css.splitlines() if line.startswith((".lines", ".midap")))
    assert not re.search(r"#[0-9a-fA-F]{3,6}|rgb|hsl|filter|shadow|gradient", rules)


def test_the_invitation_and_the_live_moment_name_the_other_place():
    now = datetime(2026, 11, 25, 12, tzinfo=UTC)
    texts = {"de": {"hero": "H", "support": "S"}, "en": {"hero": "H", "support": "S"}}
    snapshot = {"generated_at": now.isoformat(), "self": {"label": "Medienhaus"},
                "locations": [{"label": "Wien", "invite_until": None}, {"label": "Essen", "invite_until": (now + timedelta(seconds=9)).isoformat()}],
                "message": {"key": "partner_waiting", "texts": texts, "until": (now + timedelta(seconds=9)).isoformat()}}
    assert views.start(snapshot, "")["place"] == "Essen"
    assert views.live_moment("Essen", "")["place"] == "Essen"


class Screen:
    def __init__(self):
        self.states = []

    def show(self, text):
        pass

    def view(self, state):
        self.states.append(state)


class Keys:
    def __init__(self, *answers):
        self.answers = list(answers)

    def flush(self):
        pass

    def wait(self, accepted, timeout):
        return self.answers.pop(0) if self.answers else None


class Api:
    pool, printing = None, False

    def __init__(self, match=None):
        self.match = match

    def presence(self):
        return None

    def notice(self):
        return "notice-abc", "NOTICE", None

    def start(self, version, pool=None):
        return "sid", "CODE-1"

    def upload_and_process(self, sid, image):
        pass

    def status(self, sid):
        return {"status": "complete", "job": None, "match": self.match}


def done_state(api):
    screen = Screen()
    Kiosk(api, lambda: b"jpeg", screen, Keys(ENTER, ENTER), sleep=lambda _s: None, clock=iter(range(10_000)).__next__).run_once()
    return next(s for s in screen.states if s["screen"] == "done")


def test_the_code_screen_knows_whether_the_visit_was_live():
    assert done_state(Api({"kind": "live", "partner_label": "Essen"}))["live"] is True
    assert done_state(Api())["live"] is False


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_the_kiosk_estimates_the_time_left_from_finished_pictures():
    """Running for 40 s with 2 of 4 pictures done: about 40 s more (ADR-040)."""
    clock = Clock()
    states = iter([
        {"status": "processing", "job": {"status": "running", "frames_done": 0, "frames_total": 4}},
        {"status": "processing", "job": {"status": "running", "frames_done": 2, "frames_total": 4}},
        {"status": "complete", "job": None},
    ])

    class Timed(Api):
        def status(self, sid):
            return next(states)

    screen = Screen()

    def sleep(seconds):
        clock.now += 20 if seconds == 1 else 0

    Kiosk(Timed(), lambda: b"jpeg", screen, Keys(ENTER, ENTER), sleep=sleep, clock=clock).run_once()
    making = [s for s in screen.states if s["screen"] == "making" and s.get("frames")]
    assert making[0]["remaining"] == "Das erste Bild entsteht."
    assert making[1]["remaining"] == "Noch etwa eine halbe Minute."


def test_the_screens_draw_the_round_button_and_no_key_labels_or_countdown_text():
    assert "function press(" in SCREENS and 'action("ENTER"' not in SCREENS and "Noch ${" not in SCREENS
    assert SCREENS.count("top(s.label") == 1  # the header bar on the start screen only
    css = (UI / "ui.css").read_text(encoding="utf-8")
    assert ".press.pulse .cap" in css and "prefers-reduced-motion" in css


def test_a_code_written_down_in_groups_still_deletes(client):
    """The code screen shows the code in groups of four (ADR-040); spaces must not matter."""
    from photobox_server.core import db_connect
    from photobox_server.withdrawal import hash_code, new_code, session_for_code
    code, code_hash = new_code()
    assert hash_code(" ".join(code[i:i + 4] for i in range(0, len(code), 4))) == code_hash
    with db_connect() as db:
        db.execute("INSERT INTO sessions(id, box_id, status, created_at, withdrawal_hash) VALUES('s1', 'pi-01', 'complete', '2026-01-01', ?)", (code_hash,))
        assert session_for_code(db, "  " + " ".join(code[i:i + 4] for i in range(0, len(code), 4)) + " ") == "s1"


# The signature colour (ADR-043)

CSS = (UI / "ui.css").read_text(encoding="utf-8")


def _luminance(hex_colour):
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    channels = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def test_one_signature_colour_used_only_while_paired_live():
    colour = re.search(r"--live:\s*(#[0-9a-fA-F]{6})", CSS).group(1)
    assert (_luminance(colour) + 0.05) / 0.05 >= 4.5  # readable on black, also for a message
    uses = [line for line in CSS.splitlines() if "var(--live)" in line]
    assert len(uses) == 5 and all(line.startswith(".screen.live ") for line in uses)
    # no other colour than white, greys and the signature colour on the screen itself
    others = set(re.findall(r"#[0-9a-fA-F]{3,6}\b", CSS)) - {colour, "#000", "#fff", "#9a9aa0", "#2e2e33", "#141416", "#555"}
    assert not others, others


def test_the_colour_goes_to_the_pair_only(tmp_path):
    """Run the screen's own rule in Node: connected screens only, never invitations or waiting."""
    import json
    import shutil
    import subprocess
    if not shutil.which("node"):
        import pytest
        pytest.skip("Node is not installed")
    rule = re.search(r"  function connected\(state\) \{.*?\n  \}\n", SCREENS, re.S).group(0)
    cases = {
        "live_moment": ({"screen": "live_moment"}, True),
        "listening": ({"screen": "listening"}, True),
        "converting": ({"screen": "converting"}, True),
        "making live": ({"screen": "making", "encounter": {"kind": "live"}}, True),
        "making searching": ({"screen": "making", "encounter": {"kind": "searching"}}, False),
        "making alone": ({"screen": "making"}, False),
        "done shared": ({"screen": "done", "live": True}, True),
        "done alone": ({"screen": "done", "live": False}, False),
        "invite": ({"screen": "invite", "place": "Essen"}, False),
        "start with someone live": ({"screen": "start", "badge": {"kind": "live"}}, False),
        "resting live": ({"screen": "resting", "live": True}, False),
    }
    script = tmp_path / "rule.js"
    script.write_text(rule + f"\nconst cases = {json.dumps({k: v[0] for k, v in cases.items()})};\n"
                      "console.log(JSON.stringify(Object.fromEntries(Object.entries(cases).map(([k, s]) => [k, connected(s)]))));\n")
    result = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, check=True).stdout)
    assert result == {k: v[1] for k, v in cases.items()}


def test_only_the_talk_button_is_marked_for_the_colour():
    assert SCREENS.count("talk: true") == 2  # "Sprechen" while making the strip, and "Fertig" while listening
    assert 'press("Mehr Zeit", "More time", { ring: s.seconds })' in SCREENS
