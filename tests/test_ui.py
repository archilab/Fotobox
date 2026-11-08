"""The HTML screen for the box (milestone U0). It must work offline and never treat data as markup."""
import re
from pathlib import Path
import pytest

UI = Path(__file__).parent.parent / "src" / "photobox_client" / "ui"
SOURCES = ["screens.js", "app.js", "index.html", "ui.css", "prototype.html", "prototype.js"]


@pytest.mark.parametrize("name", SOURCES)
def test_nothing_is_loaded_from_outside(name):
    text = (UI / name).read_text(encoding="utf-8").replace("http://www.w3.org/2000/svg", "")
    assert not re.search(r"https?://|//cdn|@import", text)


def test_data_is_never_treated_as_markup():
    source = (UI / "screens.js").read_text(encoding="utf-8")
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(", source)


def test_the_font_and_its_licence_are_included():
    assert (UI / "fonts" / "inter-latin-wght-normal.woff2").read_bytes()[:4] == b"wOF2"
    assert "SIL Open Font License" in (UI / "fonts" / "OFL-LICENSE.txt").read_text(encoding="utf-8")


def test_every_prototype_screen_has_a_view():
    views = set(re.findall(r"^    (\w+): \(", (UI / "screens.js").read_text(encoding="utf-8"), flags=re.MULTILINE))
    used = set(re.findall(r'screen: "(\w+)"', (UI / "prototype.js").read_text(encoding="utf-8")))
    assert used and used <= views


def test_agreeing_is_blocked_until_the_last_page_has_been_seen():
    """A real notice can need two pages; ENTER on the first must not count as agreeing to both."""
    app = (UI / "app.js").read_text(encoding="utf-8")
    screens = (UI / "screens.js").read_text(encoding="utf-8")
    assert re.search(r'event\.key === "Enter"\) return window\.PhotoboxScreens\.canConfirm\(\) && send\("enter"\)', app)
    assert "seenAll = seenAll || page === count - 1" in screens and "seenAll = count === 1" in screens


@pytest.mark.parametrize("name", ["index.html", "prototype.html"])
def test_the_page_tells_the_browser_not_to_translate_it(name):
    page = (UI / name).read_text(encoding="utf-8")
    assert 'translate="no"' in page and 'content="notranslate"' in page


def test_the_header_names_the_project():
    assert '"MEET A STRANGER"' in (UI / "screens.js").read_text(encoding="utf-8")
