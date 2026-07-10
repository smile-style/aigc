import pytest

from studio.services.script import generate_episode_script, generate_script, validate_script_payload


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload
        self.messages = None
        self.temperature = None

    def generate_json(self, messages, temperature=None):
        self.messages = messages
        self.temperature = temperature
        return self.payload


def make_episode(index):
    return {
        "episode": index,
        "title": f"第{index}集标题",
        "summary": f"第{index}集摘要",
        "key_conflict": f"第{index}集冲突",
        "cliffhanger": f"第{index}集悬念",
    }


def make_outline():
    return {
        "id": "outline-1",
        "title": "爆款短剧标题",
        "core_premise": "核心设定",
        "protagonist": "主角设定",
        "hook": "强钩子",
        "arc_summary": "60集主线梗概",
    }


def make_payload():
    return {
        "script_plan": [make_episode(i) for i in range(1, 61)],
        "episode_1_script": "第1集完整剧本样稿",
    }


def test_generate_script_returns_plan_and_episode_1_script():
    provider = FakeProvider(make_payload())

    payload = generate_script(provider, make_outline())

    prompt = provider.messages[-1]["content"]
    assert len(payload["script_plan"]) == 60
    assert payload["episode_1_script"] == "第1集完整剧本样稿"
    assert "爆款短剧标题" in prompt
    assert "60" in prompt
    assert "2" in prompt
    assert "episode_1_script" in prompt
    assert "title" in prompt
    assert "summary" in prompt
    assert "key_conflict" in prompt
    assert "cliffhanger" in prompt
    assert "JSON" in provider.messages[0]["content"]
    assert provider.temperature == 0.7


@pytest.mark.parametrize("outline", [None, [], "outline"])
def test_generate_script_rejects_non_dict_outline(outline):
    provider = FakeProvider(make_payload())

    with pytest.raises(ValueError, match="outline|object|dict"):
        generate_script(provider, outline)


def test_generate_script_rejects_outline_missing_field():
    outline = make_outline()
    del outline["hook"]
    provider = FakeProvider(make_payload())

    with pytest.raises(ValueError, match="hook"):
        generate_script(provider, outline)


@pytest.mark.parametrize("value", ["", "   ", None, 123])
def test_generate_script_rejects_invalid_outline_text_field(value):
    outline = make_outline()
    outline["title"] = value
    provider = FakeProvider(make_payload())

    with pytest.raises(ValueError, match="title"):
        generate_script(provider, outline)


def test_validate_script_payload_rejects_wrong_episode_count():
    with pytest.raises(ValueError, match="Expected 60 script plan entries, got 1"):
        validate_script_payload(
            {
                "script_plan": [make_episode(1)],
                "episode_1_script": "第1集完整剧本样稿",
            }
        )


def test_validate_script_payload_rejects_missing_episode_script():
    with pytest.raises(ValueError, match="episode_1_script"):
        validate_script_payload({"script_plan": [make_episode(i) for i in range(1, 61)]})


@pytest.mark.parametrize("payload", [None, [], "nope"])
def test_validate_script_payload_rejects_non_dict_payload(payload):
    with pytest.raises(ValueError, match="object|dict"):
        validate_script_payload(payload)


@pytest.mark.parametrize("script_plan", [None, "not-a-list", {"episode": 1}])
def test_validate_script_payload_rejects_non_list_script_plan(script_plan):
    with pytest.raises(ValueError, match="script_plan"):
        validate_script_payload(
            {
                "script_plan": script_plan,
                "episode_1_script": "第1集完整剧本样稿",
            }
        )


def test_validate_script_payload_rejects_non_object_episode():
    script_plan = [make_episode(i) for i in range(1, 61)]
    script_plan[0] = 123

    with pytest.raises(ValueError, match="Episode 1 must be an object"):
        validate_script_payload(
            {
                "script_plan": script_plan,
                "episode_1_script": "第1集完整剧本样稿",
            }
        )


def test_validate_script_payload_rejects_missing_episode_field():
    script_plan = [make_episode(i) for i in range(1, 61)]
    del script_plan[0]["cliffhanger"]

    with pytest.raises(ValueError, match="cliffhanger"):
        validate_script_payload(
            {
                "script_plan": script_plan,
                "episode_1_script": "第1集完整剧本样稿",
            }
        )


@pytest.mark.parametrize("value", [1.0, True])
def test_validate_script_payload_rejects_non_int_episode_number(value):
    script_plan = [make_episode(i) for i in range(1, 61)]
    script_plan[0]["episode"] = value

    with pytest.raises(ValueError, match="Episode 1 episode|number"):
        validate_script_payload(
            {
                "script_plan": script_plan,
                "episode_1_script": "第1集完整剧本样稿",
            }
        )


def test_validate_script_payload_accepts_int_episode_number():
    payload = validate_script_payload(make_payload())

    assert payload["script_plan"][0]["episode"] == 1


def test_validate_script_payload_rejects_wrong_episode_number():
    script_plan = [make_episode(i) for i in range(1, 61)]
    script_plan[0]["episode"] = 99

    with pytest.raises(ValueError, match="Episode 1 field episode must equal 1"):
        validate_script_payload(
            {
                "script_plan": script_plan,
                "episode_1_script": "第1集完整剧本样稿",
            }
        )


@pytest.mark.parametrize("field", ["title", "summary", "key_conflict", "cliffhanger"])
@pytest.mark.parametrize("value", ["", "   ", None, 123])
def test_validate_script_payload_rejects_invalid_episode_text_fields(field, value):
    script_plan = [make_episode(i) for i in range(1, 61)]
    script_plan[0][field] = value

    with pytest.raises(ValueError, match=field):
        validate_script_payload(
            {
                "script_plan": script_plan,
                "episode_1_script": "第1集完整剧本样稿",
            }
        )


@pytest.mark.parametrize("value", ["", "   ", None, 123])
def test_validate_script_payload_rejects_invalid_episode_1_script(value):
    with pytest.raises(ValueError, match="episode_1_script"):
        validate_script_payload(
            {
                "script_plan": [make_episode(i) for i in range(1, 61)],
                "episode_1_script": value,
            }
        )

def test_generate_episode_script_uses_episode_and_continuity_context():
    provider = FakeProvider({"episode_script": "第 12 集完整剧本"})
    episode = make_episode(12)
    previous_episode = make_episode(11)
    next_episode = make_episode(13)

    result = generate_episode_script(
        provider,
        make_outline(),
        episode,
        previous_episode=previous_episode,
        next_episode=next_episode,
    )

    prompt = provider.messages[-1]["content"]
    assert result == "第 12 集完整剧本"
    assert "第 12 集" in prompt
    assert previous_episode["title"] in prompt
    assert next_episode["title"] in prompt
    assert episode["key_conflict"] in prompt


@pytest.mark.parametrize("payload", [None, {}, {"episode_script": ""}])
def test_generate_episode_script_rejects_invalid_response(payload):
    provider = FakeProvider(payload)

    with pytest.raises(ValueError, match="episode_script|object"):
        generate_episode_script(provider, make_outline(), make_episode(2))
