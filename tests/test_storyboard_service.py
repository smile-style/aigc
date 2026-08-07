import pytest

from studio.constants import (
    EPISODE_DURATION_TARGET_SECONDS,
    MAX_STORYBOARD_SHOTS,
    MIN_STORYBOARD_SHOTS,
)
from studio.services.storyboard import (
    MIN_SHOT_DURATION_SECONDS,
    REQUIRED_STORYBOARD_FIELDS,
    generate_storyboard,
    normalize_duration_seconds,
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
        "duration": "5秒",
        "duration_seconds": 5,
        "visual_description": f"分镜画面描述 {index}",
        "character_action": f"角色动作 {index}",
        "dialogue_or_narration": f"台词或旁白 {index}",
        "camera_language": f"镜头语言 {index}",
        "image_prompt": f"图像提示词 {index}",
        "video_prompt": f"视频提示词 {index}",
    }


def make_payload(count=MIN_STORYBOARD_SHOTS):
    shots = [make_shot(i) for i in range(1, count + 1)]
    base_duration, remainder = divmod(EPISODE_DURATION_TARGET_SECONDS, count)
    for index, shot in enumerate(shots):
        duration = base_duration + (1 if index < remainder else 0)
        shot["duration"] = f"{duration}秒"
        shot["duration_seconds"] = duration
    return {"storyboard_prompts": shots}


def test_generate_storyboard_returns_valid_shots():
    episode_1_script = "第一集剧本：主角在雨夜醒来，发现命运已经改写。"
    provider = FakeProvider(make_payload(MIN_STORYBOARD_SHOTS))

    prompts = generate_storyboard(provider, episode_1_script)

    prompt = provider.messages[-1]["content"]
    assert len(prompts) == MIN_STORYBOARD_SHOTS
    assert prompts[0]["shot_number"] == 1
    assert episode_1_script in prompt
    assert "JSON" in provider.messages[0]["content"]
    for field in sorted(REQUIRED_STORYBOARD_FIELDS - {"shot_number"}):
        assert field in prompt
    assert "image_prompt" in prompt
    assert "video_prompt" in prompt
    assert "无人说话时固定填写“无对白”" in prompt
    assert "不要填写旁白、音效、系统提示、画面字幕、动作或环境描述" in prompt
    assert "60 到 90 秒" in prompt
    assert "成片总时长为 75 秒" in prompt
    assert "4 到 15" in prompt
    assert provider.temperature == 0.6


@pytest.mark.parametrize("value", ["", "   ", None, 123])
def test_generate_storyboard_rejects_invalid_episode_1_script(value):
    provider = FakeProvider(make_payload(MIN_STORYBOARD_SHOTS))

    with pytest.raises(ValueError, match="episode_script"):
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


@pytest.mark.parametrize("field", sorted(REQUIRED_STORYBOARD_FIELDS - {"shot_number"}))
@pytest.mark.parametrize("value", ["", "   ", None, 123])
def test_validate_storyboard_payload_rejects_invalid_text_fields(field, value):
    payload = make_payload(MIN_STORYBOARD_SHOTS)
    payload["storyboard_prompts"][0][field] = value

    with pytest.raises(ValueError, match=field):
        validate_storyboard_payload(payload)


@pytest.mark.parametrize("value", [1.0, True, None])
def test_validate_storyboard_payload_rejects_invalid_shot_number_type(value):
    payload = make_payload(MIN_STORYBOARD_SHOTS)
    payload["storyboard_prompts"][0]["shot_number"] = value

    with pytest.raises(ValueError, match="shot_number|整数"):
        validate_storyboard_payload(payload)


def test_validate_storyboard_payload_normalizes_string_shot_number():
    payload = make_payload(MIN_STORYBOARD_SHOTS)
    payload["storyboard_prompts"][0]["shot_number"] = "镜头 1"

    prompts = validate_storyboard_payload(payload)

    assert prompts[0]["shot_number"] == 1


def test_validate_storyboard_payload_rejects_wrong_shot_number_sequence():
    payload = make_payload(MIN_STORYBOARD_SHOTS)
    payload["storyboard_prompts"][0]["shot_number"] = 99

    with pytest.raises(ValueError, match="shot_number must equal 1"):
        validate_storyboard_payload(payload)


def test_validate_storyboard_payload_rejects_total_duration_outside_range():
    payload = make_payload()
    for shot in payload["storyboard_prompts"]:
        shot["duration"] = f"{MIN_SHOT_DURATION_SECONDS}秒"
        shot["duration_seconds"] = MIN_SHOT_DURATION_SECONDS

    with pytest.raises(ValueError, match="总时长|duration"):
        validate_storyboard_payload(payload)


def test_validate_storyboard_payload_matches_expected_duration():
    with pytest.raises(ValueError, match="110.*75"):
        validate_storyboard_payload(make_payload(), expected_duration_seconds=110)


def test_normalize_duration_rejects_clips_shorter_than_seedance_minimum():
    with pytest.raises(ValueError, match="4 到 15"):
        normalize_duration_seconds(3, 1)


def test_validate_storyboard_payload_accepts_content_driven_duration():
    prompts = validate_storyboard_payload(
        make_payload(),
        expected_duration_seconds=EPISODE_DURATION_TARGET_SECONDS,
    )

    assert sum(item["duration_seconds"] for item in prompts) == EPISODE_DURATION_TARGET_SECONDS


def test_short_drama_v3_binds_cold_open_to_a_body_shot_without_adding_a_shot():
    payload = make_payload()
    durations = [6] * len(payload["storyboard_prompts"])
    durations[-1] -= sum(durations) - 72
    beat_ids = [
        "beat_02_protagonist_goal",
        "beat_02_protagonist_goal",
        "beat_03_obstacle_1",
        "beat_03_obstacle_1",
        "beat_04_obstacle_2",
        "beat_04_obstacle_2",
        "beat_04_obstacle_2",
        "beat_05_resolution_or_reversal",
        "beat_05_resolution_or_reversal",
        "beat_05_resolution_or_reversal",
        "beat_06_next_crisis",
        "beat_06_next_crisis",
    ]
    for index, (shot, duration, beat_id) in enumerate(
        zip(payload["storyboard_prompts"], durations, beat_ids),
        start=1,
    ):
        shot["duration"] = f"{duration}秒"
        shot["duration_seconds"] = duration
        shot["beat_id"] = beat_id
    payload["cold_open"] = {
        "source_beat_id": "beat_05_resolution_or_reversal",
        "source_shot_number": 9,
        "trim_start_ms": 1500,
        "trim_end_ms": 4500,
    }
    pacing = {
        "duration_seconds": 75,
        "cold_open": {
            "hook_type": "reversal_dialogue",
            "source_beat_id": "beat_05_resolution_or_reversal",
            "duration_seconds": 3,
            "withheld_reveal": "隐藏真相",
            "return_bridge": "两小时前",
        },
        "beats": [
            {"beat_id": "beat_01_crisis_open", "beat_type": "crisis_open"},
            {"beat_id": "beat_02_protagonist_goal", "beat_type": "protagonist_goal"},
            {"beat_id": "beat_03_obstacle_1", "beat_type": "obstacle_1"},
            {"beat_id": "beat_04_obstacle_2", "beat_type": "obstacle_2"},
            {"beat_id": "beat_05_resolution_or_reversal", "beat_type": "resolution_or_reversal"},
            {"beat_id": "beat_06_next_crisis", "beat_type": "next_crisis"},
        ],
    }

    result = generate_storyboard(
        FakeProvider(payload),
        "完整剧本",
        pacing=pacing,
        include_metadata=True,
    )

    assert len(result["storyboard_prompts"]) == MIN_STORYBOARD_SHOTS
    assert sum(item["duration_seconds"] for item in result["storyboard_prompts"]) == 72
    assert result["cold_open"]["source_shot_number"] == 9
    assert result["cold_open"]["trim_end_ms"] - result["cold_open"]["trim_start_ms"] == 3000
