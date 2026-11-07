import re
from collections import Counter
import pytest
from PIL import Image
from photobox_server.prompts import BACKDROPS, prompts_for
from photobox_server.stories import STORIES
from photobox_server.strip import dated_footer, render_strip

SEEDS = range(len(BACKDROPS) * len(STORIES))


def test_there_are_one_hundred_distinct_stories():
    assert len(STORIES) == 100
    assert len({story[0] for story in STORIES}) == 100
    assert len({beat for story in STORIES for beat in story[1:]}) == 400


@pytest.mark.parametrize("story", STORIES, ids=[s[0] for s in STORIES])
def test_story_form(story):
    title, *beats = story
    assert len(beats) == 4
    assert "{a}" in beats[0] and "{b}" in beats[0], "the opening must show both people"
    for beat in beats:
        assert set(re.findall(r"{(\w*)}", beat)) <= {"a", "b"}
        # The scene introduces both people, so a beat may also refer to them together.
        assert re.search(r"{[ab]}|\b(both|them)\b", beat, re.IGNORECASE)
        assert beat.format(a="A", b="B")


@pytest.mark.parametrize("story", STORIES, ids=[s[0] for s in STORIES])
def test_stories_stay_suitable_for_strangers(story):
    text = " ".join(story[1:]).lower()
    forbidden = r"\b(kiss\w*|hug|hugs|hugging|embrac\w*|romantic|cuddl\w*|lovers?|couples?|guns?|knife|knives|swords?|blood\w*|naked|nude|drunk|beer|wine|cigar\w*|child\w*|kids?|fat|ugly|stupid)\b"
    assert not re.search(forbidden, text)


@pytest.mark.parametrize("frames", [3, 4])
def test_one_prompt_per_frame_with_one_backdrop(frames):
    prompts = prompts_for(frames, 7)
    backdrop = BACKDROPS[7 % len(BACKDROPS)]
    assert len(prompts) == frames == len(set(prompts))
    assert all(f"{backdrop} backdrop" in prompt for prompt in prompts)


def test_prompts_contain_no_unfilled_placeholders():
    for seed in SEEDS:
        for prompt in prompts_for(4, seed):
            assert "{" not in prompt and "}" not in prompt


def test_every_story_and_backdrop_combination_is_reachable_evenly():
    used = Counter((seed % len(BACKDROPS), prompts_for(4, seed)[1].split("This moment: ")[-1]) for seed in SEEDS)
    assert len(used) == len(BACKDROPS) * len(STORIES)
    assert set(used.values()) == {1}


def test_story_follows_the_seed():
    assert prompts_for(4, 11) == prompts_for(4, 11)
    assert prompts_for(4, 11) != prompts_for(4, 15)


@pytest.mark.parametrize("seed", [0, 37, 399])
def test_opening_shows_both_faces_and_main_characters_are_named(seed):
    prompts = prompts_for(4, seed)
    assert "Both faces are clearly visible" in prompts[0]
    assert all("The main characters are the person from Image 1 and the person from Image 2" in p for p in prompts)
    assert "both faces clear" in prompts[0]


def test_other_people_are_not_forbidden():
    assert all("nobody else" not in p for seed in SEEDS for p in prompts_for(4, seed))
    text = " ".join(" ".join(s[1:]) for s in STORIES).lower()
    assert "stranger" in text or "adults" in text


def test_three_frames_keep_the_opening_development_and_payoff():
    moment = lambda prompts: [p.split("This moment: ")[1] for p in prompts]
    four, three = moment(prompts_for(4, 5)), moment(prompts_for(3, 5))
    assert three == [four[0], four[1], four[3]]


def staging(prompt):
    return prompt.split("Staging: ")[1].split(" This moment: ")[0]


@pytest.mark.parametrize("seed", range(0, 400, 7))
def test_every_frame_gets_its_own_staging(seed):
    prompts = prompts_for(4, seed)
    stagings = [staging(p).replace("Image 1", "X").replace("Image 2", "X") for p in prompts]
    assert len(set(stagings)) == 4
    assert all("Image 1" in staging(p) and "Image 2" in staging(p) for p in prompts)


def test_stagings_vary_across_strips_and_either_person_can_be_in_front():
    firsts = {staging(prompts_for(4, seed)[1]) for seed in range(200)}
    assert len(firsts) > 12
    leads = {staging(prompts_for(4, seed)[2]).startswith("the person from Image 1") for seed in range(50)}
    assert leads == {True, False}


@pytest.mark.parametrize("seed", range(0, 400, 3))
def test_the_camera_never_moves(seed):
    text = " ".join(prompts_for(4, seed)).lower()
    assert "fixed at eye level" in text
    assert not re.search(r"(tilted|seen from above|overhead|bird's-eye|low angle|high angle|dutch angle|camera moves)\b", text)


def test_footer_carries_the_date():
    assert dated_footer("PHOTOBOX").startswith("PHOTOBOX  ")
    frames = [Image.linear_gradient("L").resize((1024, 768)).convert("RGB")] * 4
    result = render_strip(frames, dated_footer("PHOTOBOX"))
    assert result.width == 560 and result.height < 560 * 4
