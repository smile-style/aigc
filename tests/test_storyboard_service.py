import pytest

from studio.constants import MAX_STORYBOARD_SHOTS, MIN_STORYBOARD_SHOTS
from studio.services.storyboard import (
    REQUIRED_STORYBOARD_FIELDS,
    generate_storyboard,
    validate_storyboard_payload,
)


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload
        self.messages = None
        self.temperature = None

    def generate_json(self, messages, temperature=None):
        self.messages = messages
        self.temperature = temperature
        return self.payload


def make_shot(index):
    return {
        "shot_number": index,
        "duration": f"{index + 2}秒",
        "visual_description": f"分镜画面描述 {index}",
        "character_action": f"角色动作 {index}",
        "dialogue_or_narration": f"台词或旁白 {index}",
        "camera_language": f"镜头语言 {index}",
        "image_prompt": f"图像提示词 {index}",
        "video_prompt": f"视频提示词 {index}",
    }


def make_payload(count=MIN_STORYBOARD_SHOTS):
    return {"storyboard_prompts": [make_shot(i) for i in range(1, count + 1)]}


def test_generate_storyboard_returns_valid_shots():
    episode_1_script = "第1集剧本：主角在雨夜醒来，发现命运已经改写。"
    provider = FakeProvider(make_payload(MIN_STORYBOARD_SHOTS))

    prompts = generate_storyboard(provider, episode_1_script)

    prompt = provider.messages[-1]["content"]
    assert len(prompts) == MIN_STORYBOARD_SHOTS
    assert prompts[0]["shot_number"] == 1
    assert episode_1_script in prompt
    assert f"{MIN_STORYBOARD_SHOTS}到{MAX_STORYBOARD_SHOTS}" in prompt
    assert "JSON" in provider.messages[0]["content"]
    for field in sorted(REQUIRED_STORYBOARD_FIELDS - {"shot_number"}):
        assert field in prompt
    assert "image_prompt" in prompt
    assert "video_prompt" in prompt
    assert provider.temperature == 0.6


@pytest.mark.parametrize("value", ["", "   ", None, 123])
def test_generate_storyboard_rejects_invalid_episode_1_script(value):
    provider = FakeProvider(make_payload(MIN_STORYBOARD_SHOTS))

    with pytest.raises(ValueError, match="episode_1_script"):
        generate_storyboard(provider, value)


def test_validate_storyboard_payload_rejects_too_few_shots():
    with pytest.raises(ValueError, match=str(MIN_STORYBOARD_SHOTS - 1)):
        validate_storyboard_payload(make_payload(MIN_STORYBOARD_SHOTS - 1))


def test_validate_storyboard_payload_rejects_too_many_shots():
    with pytest.raises(ValueError, match=str(MAX_STORYBOARD_SHOTS + 1)):
        validate_storyboard_payload(make_payload(MAX_STORYBOARD_SHOTS + 1))


def test_validate_storyboard_payload_accepts_upper_bound():
    prompts = validate_storyboard_payload(make_payload(MAX_STORYBOARD_SHOTS))

    assert len(prompts) == MAX_STORYBOARD_SHOTS
    assert prompts[-1]["shot_number"] == MAX_STORYBOARD_SHOTS


@pytest.mark.parametrize("payload", [None, [], "bad"])
def test_validate_storyboard_payload_rejects_non_dict_payload(payload):
    with pytest.raises(ValueError, match="object|dict"):
        validate_storyboard_payload(payload)


def test_validate_storyboard_payload_rejects_missing_storyboard_prompts_key():
    with pytest.raises(ValueError, match="storyboard_prompts|list"):
        validate_storyboard_payload({})


@pytest.mark.parametrize("prompts", [None, "bad", {"shot_number": 1}])
def test_validate_storyboard_payload_rejects_non_list_storyboard_prompts(prompts):
    with pytest.raises(ValueError, match="storyboard_prompts"):
        validate_storyboard_payload({"storyboard_prompts": prompts})


def test_validate_storyboard_payload_rejects_non_object_shot():
    payload = make_payload(MIN_STORYBOARD_SHOTS)
    payload["storyboard_prompts"][0] = 123

    with pytest.raises(ValueError, match="Shot 1 must be an object"):
        validate_storyboard_payload(payload)


def test_validate_storyboard_payload_rejects_missing_prompt_field():
    payload = make_payload(MIN_STORYBOARD_SHOTS)
    del payload["storyboard_prompts"][0]["video_prompt"]

    with pytest.raises(ValueError, match="video_prompt"):
        validate_storyboard_payload(payload)


@pytest.mark.parametrize(
    "field",
    sorted(REQUIRED_STORYBOARD_FIELDS - {"shot_number"}),
)
@pytest.mark.parametrize("value", ["", "   ", None, 123])
def test_validate_storyboard_payload_rejects_invalid_text_fields(field, value):
    payload = make_payload(MIN_STORYBOARD_SHOTS)
    payload["storyboard_prompts"][0][field] = value

    with pytest.raises(ValueError, match=field):
        validate_storyboard_payload(payload)


@pytest.mark.parametrize("value", [1.0, True, "1", None])
def test_validate_storyboard_payload_rejects_invalid_shot_number_type(value):
    payload = make_payload(MIN_STORYBOARD_SHOTS)
    payload["storyboard_prompts"][0]["shot_number"] = value

    with pytest.raises(ValueError, match="shot_number|integer|number"):
        validate_storyboard_payload(payload)


def test_validate_storyboard_payload_rejects_wrong_shot_number_sequence():
    payload = make_payload(MIN_STORYBOARD_SHOTS)
    payload["storyboard_prompts"][0]["shot_number"] = 99

    with pytest.raises(ValueError, match="shot_number must equal 1"):
        validate_storyboard_payload(payload)
