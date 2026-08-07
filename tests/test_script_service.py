import pytest

from studio.services.script import (
    estimate_spoken_duration_seconds,
    generate_episode_script,
    generate_script,
    validate_continuity_payload,
    validate_episode_pacing,
    validate_script_duration_capacity,
    validate_script_payload,
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


class SequenceProvider:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []

    def generate_json(self, messages, temperature=None):
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
            }
        )
        return self.payloads.pop(0)


def make_episode(index):
    return {
        "episode": index,
        "title": f"第{index}集标题",
        "summary": f"第{index}集摘要",
        "key_conflict": f"第{index}集冲突",
        "cliffhanger": f"第{index}集悬念",
        "episode_goal": f"第{index}集主角目标",
        "obstacle_1": f"第{index}集第一次阻碍",
        "obstacle_2": f"第{index}集第二次阻碍",
        "resolution_or_reversal": f"第{index}集解决或反转",
        "next_crisis": f"第{index}集结尾新危机",
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


def make_pacing(duration_seconds=75, profile_version=None):
    goal_end = max(12, duration_seconds // 6)
    obstacle_1_end = max(goal_end + 8, round(duration_seconds * 0.38))
    obstacle_2_end = max(obstacle_1_end + 8, round(duration_seconds * 0.62))
    next_crisis_start = duration_seconds - 7
    pacing = {
        "duration_seconds": duration_seconds,
        "cold_open": {
            "hook_type": "reversal_dialogue",
            "source_beat_id": "beat_05_resolution_or_reversal",
            "duration_seconds": 3,
            "withheld_reveal": "真正签约人的身份",
            "return_bridge": "两小时前",
        },
        "beats": [
            {"beat_id": "beat_01_crisis_open", "beat_type": "crisis_open", "start_second": 0, "end_second": 3, "event": "危机结果前置"},
            {"beat_id": "beat_02_protagonist_goal", "beat_type": "protagonist_goal", "start_second": 3, "end_second": goal_end, "event": "主角明确目标"},
            {"beat_id": "beat_03_obstacle_1", "beat_type": "obstacle_1", "start_second": goal_end, "end_second": obstacle_1_end, "event": "第一次阻碍"},
            {"beat_id": "beat_04_obstacle_2", "beat_type": "obstacle_2", "start_second": obstacle_1_end, "end_second": obstacle_2_end, "event": "第二次阻碍升级"},
            {"beat_id": "beat_05_resolution_or_reversal", "beat_type": "resolution_or_reversal", "start_second": obstacle_2_end, "end_second": next_crisis_start, "event": "解决问题并反转"},
            {"beat_id": "beat_06_next_crisis", "beat_type": "next_crisis", "start_second": next_crisis_start, "end_second": duration_seconds, "event": "下一集新危机"},
        ],
        "information_beats": [
            {"at_second": second, "information": f"新信息 {index}", "consequence": f"后果 {index}"}
            for index, second in enumerate(range(0, duration_seconds + 1, 10), start=1)
        ],
    }
    if profile_version:
        pacing["profile_version"] = profile_version
    return pacing


def make_continuity(carry_in=None):
    return {
        "carry_in": {} if carry_in is None else carry_in,
        "state_delta": {"goal": "主角作出不可逆选择"},
        "carry_out": {"goal": "主角必须承担选择的后果"},
    }


def make_payload():
    return {
        "script_plan": [make_episode(i) for i in range(1, 61)],
        "episode_1_script": "第1集完整剧本样稿",
        "episode_1_pacing": make_pacing(),
        "episode_1_continuity": make_continuity(),
    }


def test_generate_script_returns_plan_and_episode_1_script():
    provider = FakeProvider(make_payload())

    payload = generate_script(provider, make_outline())

    prompt = provider.messages[-1]["content"]
    assert len(payload["script_plan"]) == 60
    assert payload["episode_1_script"] == "第1集完整剧本样稿"
    assert payload["episode_1_pacing"]["duration_seconds"] == 75
    assert payload["episode_1_continuity"]["carry_in"] == {}
    assert payload["pacing_profile_version"] == "short_drama_v3"
    assert "爆款短剧标题" in prompt
    assert "60" in prompt
    assert "60 到 90" in prompt
    assert "75 秒" in prompt
    assert "3.5" in prompt
    assert "不得超过 10 秒" in prompt
    assert "source_beat_id" in prompt
    assert "return_bridge" in prompt
    assert "5到8秒" in prompt
    assert "episode_1_continuity" in prompt
    assert "episode_1_script" in prompt
    assert "title" in prompt
    assert "summary" in prompt
    assert "key_conflict" in prompt
    assert "cliffhanger" in prompt
    assert "JSON" in provider.messages[0]["content"]
    assert provider.temperature == 0.7


def test_generate_script_compresses_overloaded_episode_1_once():
    overloaded = make_payload()
    overloaded["episode_1_script"] = "\n".join(
        f"苏晚：{'这段对白没有推进剧情' * 8}" for _ in range(12)
    )
    corrected = make_payload()
    corrected["episode_1_script"] = "苏晚：合同只能由我签。"
    provider = SequenceProvider(overloaded, corrected)

    result = generate_script(provider, make_outline())

    assert result["episode_1_script"] == corrected["episode_1_script"]
    assert len(provider.calls) == 2
    assert provider.calls[1]["temperature"] == 0.4


def test_generate_script_fails_when_only_compression_is_still_overloaded():
    overloaded = make_payload()
    overloaded["episode_1_script"] = "\n".join(
        f"苏晚：{'这段对白没有推进剧情' * 8}" for _ in range(12)
    )
    provider = SequenceProvider(overloaded, overloaded)

    with pytest.raises(ValueError, match="至少需要"):
        generate_script(provider, make_outline())

    assert len(provider.calls) == 2


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


@pytest.mark.parametrize(("start_second", "end_second"), [(1, 3), (0, 5), (4, 7)])
def test_validate_script_payload_normalizes_model_crisis_open_timing(
    start_second,
    end_second,
):
    payload = make_payload()
    payload["episode_1_pacing"]["beats"][0]["start_second"] = start_second
    payload["episode_1_pacing"]["beats"][0]["end_second"] = end_second
    payload["episode_1_pacing"]["beats"][1]["start_second"] = end_second

    result = validate_script_payload(payload)

    crisis, goal = result["episode_1_pacing"]["beats"][:2]
    assert crisis["start_second"] == 0
    assert crisis["end_second"] == 3
    assert goal["start_second"] == 3



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


@pytest.mark.parametrize(
    "field",
    [
        "title",
        "summary",
        "key_conflict",
        "cliffhanger",
        "episode_goal",
        "obstacle_1",
        "obstacle_2",
        "resolution_or_reversal",
        "next_crisis",
    ],
)
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
                "episode_1_pacing": make_pacing(),
            }
        )

def test_generate_episode_script_uses_episode_and_continuity_context():
    previous_carry_out = {"prop": "合同在主角手中"}
    provider = FakeProvider(
        {
            "episode_script": "第 12 集完整剧本",
            "pacing": make_pacing(),
            "continuity": make_continuity(previous_carry_out),
        }
    )
    episode = make_episode(12)
    previous_episode = make_episode(11)
    next_episode = make_episode(13)

    result = generate_episode_script(
        provider,
        make_outline(),
        episode,
        previous_episode=previous_episode,
        next_episode=next_episode,
        continuity_context={"carry_out": previous_carry_out},
    )

    prompt = provider.messages[-1]["content"]
    assert result["episode_script"] == "第 12 集完整剧本"
    assert result["pacing"]["duration_seconds"] == 75
    assert result["continuity"]["carry_in"] == previous_carry_out
    assert "第 12 集" in prompt
    assert previous_episode["title"] in prompt
    assert next_episode["title"] in prompt
    assert episode["key_conflict"] in prompt


@pytest.mark.parametrize("payload", [None, {}, {"episode_script": ""}])
def test_generate_episode_script_rejects_invalid_response(payload):
    provider = FakeProvider(payload)

    with pytest.raises(ValueError, match="episode_script|object"):
        generate_episode_script(provider, make_outline(), make_episode(2))


def test_generate_episode_script_compresses_overloaded_script_once():
    episode_script = "【0—3秒】\n" + "\n".join(
        f"苏晚：{'这段对白需要自然表达和情绪停顿' * 6}" for _ in range(10)
    ) + "\n【100—120秒】"
    required = estimate_spoken_duration_seconds(episode_script)
    corrected = {
        "episode_script": "苏晚：合同只能由我签。",
        "pacing": make_pacing(),
        "continuity": make_continuity(),
    }
    provider = SequenceProvider(
        {"episode_script": episode_script, "pacing": make_pacing(), "continuity": make_continuity()},
        corrected,
    )

    result = generate_episode_script(provider, make_outline(), make_episode(5))

    assert required > 90
    assert result["episode_script"] == corrected["episode_script"]
    assert result["pacing"]["duration_seconds"] == 75
    assert len(provider.calls) == 2
    assert provider.calls[0]["temperature"] == 0.7
    assert provider.calls[1]["temperature"] == 0.4
    correction = provider.calls[1]["messages"][-1]["content"]
    assert "删除重复" in correction
    assert "不得扩展时间轴" in correction
    assert "90 秒" in correction


def test_generate_episode_script_compresses_overlong_timeline_once():
    provider = SequenceProvider(
        {
            "episode_script": "苏晚：合同只能由我签。",
            "pacing": make_pacing(120),
            "continuity": make_continuity(),
        },
        {
            "episode_script": "苏晚：合同只能由我签。",
            "pacing": make_pacing(),
            "continuity": make_continuity(),
        },
    )

    result = generate_episode_script(provider, make_outline(), make_episode(5))

    assert result["pacing"]["duration_seconds"] == 75
    assert len(provider.calls) == 2
    assert "时间轴为 120 秒" in provider.calls[1]["messages"][-1]["content"]


def test_generate_episode_script_normalizes_long_next_crisis():
    pacing = make_pacing()
    pacing["beats"][-2]["end_second"] = 55
    pacing["beats"][-1]["start_second"] = 55
    provider = FakeProvider(
        {
            "episode_script": "苏晚：债局已经落下。",
            "pacing": pacing,
            "continuity": make_continuity(),
        }
    )

    result = generate_episode_script(provider, make_outline(), make_episode(5))

    assert result["pacing"]["beats"][-1]["start_second"] == 67
    assert result["pacing"]["beats"][-1]["end_second"] == 75
    assert result["pacing"]["beats"][-2]["end_second"] == 67


@pytest.mark.parametrize("duration_seconds", [59, 91, True])
def test_validate_episode_pacing_rejects_duration_outside_range(duration_seconds):
    with pytest.raises(ValueError, match="duration_seconds"):
        validate_episode_pacing(make_pacing(duration_seconds))


@pytest.mark.parametrize("duration_seconds", [60, 75, 90])
def test_validate_episode_pacing_accepts_short_drama_boundaries(duration_seconds):
    pacing = validate_episode_pacing(make_pacing(duration_seconds))

    assert pacing["duration_seconds"] == duration_seconds
    assert pacing["profile_version"] == "short_drama_v3"


@pytest.mark.parametrize("tail_duration", [5, 8])
def test_validate_episode_pacing_accepts_tail_hook_boundaries(tail_duration):
    pacing = make_pacing()
    next_crisis_start = pacing["duration_seconds"] - tail_duration
    pacing["beats"][-2]["end_second"] = next_crisis_start
    pacing["beats"][-1]["start_second"] = next_crisis_start

    validated = validate_episode_pacing(pacing)

    tail_hook = validated["beats"][-1]
    assert tail_hook["end_second"] - tail_hook["start_second"] == tail_duration


def test_validate_episode_pacing_rejects_missing_required_beat():
    pacing = make_pacing()
    pacing["beats"].pop(2)

    with pytest.raises(ValueError, match="beats|obstacle_1"):
        validate_episode_pacing(pacing)


def test_validate_episode_pacing_rejects_information_gap_over_10_seconds():
    pacing = make_pacing()
    pacing["information_beats"] = [
        {"at_second": 0, "information": "开场", "consequence": "主角受困"},
        {"at_second": 20, "information": "迟到的信息", "consequence": "风险升级"},
    ]

    with pytest.raises(ValueError, match="10"):
        validate_episode_pacing(pacing)


def test_validate_episode_pacing_accepts_legacy_content_adaptive_v2():
    pacing = make_pacing(232, profile_version="content_adaptive_v2")
    pacing["beats"][-2]["end_second"] = 217
    pacing["beats"][-1]["start_second"] = 217
    pacing["information_beats"] = [
        {"at_second": second, "information": f"信息 {second}", "consequence": "推进"}
        for second in range(0, 233, 12)
    ]
    pacing = validate_episode_pacing(pacing)

    assert pacing["duration_seconds"] == 232
    assert pacing["beats"][3]["end_second"] != 45
    assert pacing["beats"][-1]["start_second"] == 217


def test_validate_episode_pacing_accepts_information_at_exact_ending():
    pacing = make_pacing()
    pacing["information_beats"].append(
        {
            "at_second": pacing["duration_seconds"],
            "information": "片尾新危机",
            "consequence": "下一集必须处理",
        }
    )

    validated = validate_episode_pacing(pacing)

    assert validated["information_beats"][-1]["at_second"] == 75


def test_validate_episode_pacing_requires_cold_open_source_and_bridge():
    pacing = make_pacing()
    del pacing["cold_open"]["return_bridge"]

    with pytest.raises(ValueError, match="return_bridge"):
        validate_episode_pacing(pacing)


@pytest.mark.parametrize(
    "field",
    ["hook_type", "source_beat_id", "withheld_reveal", "return_bridge"],
)
def test_validate_episode_pacing_requires_cold_open_text_fields(field):
    pacing = make_pacing()
    pacing["cold_open"][field] = ""

    with pytest.raises(ValueError, match=field):
        validate_episode_pacing(pacing)


def test_validate_episode_pacing_requires_exact_stable_beat_ids():
    pacing = make_pacing()
    pacing["beats"][4]["beat_id"] = "resolution"

    with pytest.raises(ValueError, match="beat_id"):
        validate_episode_pacing(pacing)


def test_validate_episode_pacing_rejects_cold_open_source_from_early_beat():
    pacing = make_pacing()
    pacing["cold_open"]["source_beat_id"] = "beat_02_protagonist_goal"

    with pytest.raises(ValueError, match="后 40%|final 40%"):
        validate_episode_pacing(pacing)


def test_validate_continuity_payload_rejects_mismatched_carry_in():
    with pytest.raises(ValueError, match="carry_in"):
        validate_continuity_payload(
            make_continuity({"prop": "错误持有人"}),
            expected_carry_in={"prop": "合同在主角手中"},
        )


@pytest.mark.parametrize("field", ["carry_in", "state_delta", "carry_out"])
def test_validate_continuity_payload_requires_object_fields(field):
    continuity = make_continuity()
    continuity[field] = "not-an-object"

    with pytest.raises(ValueError, match=field):
        validate_continuity_payload(continuity)


def test_spoken_duration_estimate_rejects_overloaded_timeline():
    episode_script = "\n".join(
        f"苏晚：{'这段对白需要自然表达和情绪停顿' * 3}" for _ in range(10)
    )
    required = estimate_spoken_duration_seconds(episode_script)

    assert required > 120
    with pytest.raises(ValueError, match="至少需要"):
        validate_script_duration_capacity(episode_script, 120)


def test_spoken_duration_estimate_allows_small_estimation_difference():
    episode_script = "\n".join(
        f"苏晚：{'这段对白需要自然表达和情绪停顿' * 3}" for _ in range(10)
    )
    required = estimate_spoken_duration_seconds(episode_script)

    assert validate_script_duration_capacity(episode_script, required - 10) == required


def test_spoken_duration_estimate_ignores_stage_directions():
    episode_script = "画面：苏晚走进仓库\n镜头：缓慢推进\n苏晚：货物终于到了"

    assert estimate_spoken_duration_seconds(episode_script) < 20
