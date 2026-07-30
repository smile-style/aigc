import json
import math
import re

from studio.constants import (
    EPISODE_COUNT,
    EPISODE_DURATION_MAX_SECONDS,
    EPISODE_DURATION_MIN_SECONDS,
    PACING_PROFILE_VERSION,
)


LEGACY_EPISODE_FIELDS = {"episode", "title", "summary", "key_conflict", "cliffhanger"}
PACING_PLAN_FIELDS = {
    "episode_goal",
    "obstacle_1",
    "obstacle_2",
    "resolution_or_reversal",
    "next_crisis",
}
REQUIRED_EPISODE_FIELDS = LEGACY_EPISODE_FIELDS | PACING_PLAN_FIELDS
REQUIRED_EPISODE_FIELD_LIST = ", ".join(sorted(REQUIRED_EPISODE_FIELDS))
REQUIRED_OUTLINE_FIELDS = {
    "title",
    "core_premise",
    "protagonist",
    "hook",
    "arc_summary",
}
REQUIRED_BEAT_TYPES = [
    "crisis_open",
    "protagonist_goal",
    "obstacle_1",
    "obstacle_2",
    "resolution_or_reversal",
    "next_crisis",
]
SPOKEN_CHARACTERS_PER_SECOND = 3.5
SPEAKER_PAUSE_SECONDS = 0.35
BASE_ACTION_ALLOWANCE_SECONDS = 10
DURATION_ESTIMATE_TOLERANCE_RATIO = 0.05
DURATION_ESTIMATE_TOLERANCE_SECONDS = 10

class ScriptDurationCapacityError(ValueError):
    def __init__(self, required_seconds, duration_seconds):
        self.required_seconds = required_seconds
        self.duration_seconds = duration_seconds
        super().__init__(
            "剧本对白、旁白和必要停顿至少需要 "
            f"{required_seconds} 秒，当前 pacing 只有 {duration_seconds} 秒"
        )

NON_SPOKEN_LINE_PREFIXES = (
    "画面",
    "镜头",
    "特写",
    "动作",
    "场景",
    "字幕",
    "转场",
    "时间",
    "环境",
    "音效",
)


def generate_script(provider, outline):
    validated_outline = validate_outline(outline)
    payload = provider.generate_json(
        [
            {
                "role": "system",
                "content": (
                    "你是爆款 AI 漫画短剧编剧。只返回 JSON，不要返回 Markdown。"
                    "所有剧情必须先按时间轴设计，再扩写剧本。"
                    'JSON 格式为 {"script_plan": [...], "episode_1_script": "...", '
                    '"episode_1_pacing": {"duration_seconds": 120, "beats": [...], '
                    '"information_beats": [...]}}。'
                ),
            },
            {
                "role": "user",
                "content": (
                    "请基于以下大纲生成完整分集规划和第 1 集完整剧本样稿。\n"
                    f"标题：{validated_outline['title']}\n"
                    f"核心设定：{validated_outline['core_premise']}\n"
                    f"主角：{validated_outline['protagonist']}\n"
                    f"钩子：{validated_outline['hook']}\n"
                    f"主线梗概：{validated_outline['arc_summary']}\n"
                    f"固定规格：共 {EPISODE_COUNT} 集；单集时长必须根据本集实际内容自然估算。"
                    f"{EPISODE_DURATION_MIN_SECONDS} 到 {EPISODE_DURATION_MAX_SECONDS} 秒仅为安全边界，"
                    "不是必须靠近的目标。\n"
                    f"script_plan 必须包含 {EPISODE_COUNT} 条，每条都必须包含字段："
                    f"{REQUIRED_EPISODE_FIELD_LIST}。\n"
                    f"episode 字段必须按 1 到 {EPISODE_COUNT} 递增，且必须是整数。\n"
                    "每集规划必须分别写明主角目标、第一次阻碍、升级后的第二次阻碍、"
                    "解决或反转，以及只留到下一集处理的新危机。\n"
                    "先按每秒约 3.5 个汉字估算对白与旁白时长，再为人物动作、镜头停留、"
                    "转场和情绪停顿额外预留至少 25% 时间；内容较多就增加总时长，"
                    "不得只修改时间标签或压缩语速。episode_1_pacing 必须让危机在开场"
                    "3秒内出现，随后尽早明确目标，再按内容需要分配两次阻碍和反转，"
                    "最后10到30秒用于下一集新危机。\n"
                    "beats 必须按 crisis_open、protagonist_goal、obstacle_1、obstacle_2、"
                    "resolution_or_reversal、next_crisis 排列，每项包含 beat_type、"
                    "start_second、end_second、event。\n"
                    "information_beats 每项包含 at_second、information、consequence；"
                    "从开场到结尾每 10 到 15 秒必须出现一个会改变认知、风险或行动的新信息，"
                    "任意相邻信息点及首尾空档不得超过 15 秒。\n"
                    "episode_1_script 必须包含场景、人物动作、对白和旁白，并严格对应节拍表。"
                ),
            },
        ],
        temperature=0.7,
    )
    return validate_script_payload(payload)


def validate_outline(outline):
    if not isinstance(outline, dict):
        raise ValueError("Outline must be an object")

    missing = REQUIRED_OUTLINE_FIELDS - set(outline)
    if missing:
        raise ValueError(f"Outline missing fields: {', '.join(sorted(missing))}")

    validated_outline = {}
    for field in sorted(REQUIRED_OUTLINE_FIELDS):
        value = outline[field]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Outline field {field} must be a non-empty string")
        validated_outline[field] = value

    return validated_outline


def validate_script_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("Model response must be an object")

    script_plan = payload.get("script_plan")
    if not isinstance(script_plan, list):
        raise ValueError("Model response must include a script_plan list")
    if len(script_plan) != EPISODE_COUNT:
        raise ValueError(
            f"Expected {EPISODE_COUNT} script plan entries, got {len(script_plan)}"
        )

    episode_1_script = payload.get("episode_1_script")
    if not isinstance(episode_1_script, str) or not episode_1_script.strip():
        raise ValueError("Model response must include a non-empty episode_1_script")

    for index, episode in enumerate(script_plan, start=1):
        if not isinstance(episode, dict):
            raise ValueError(f"Episode {index} must be an object")

        missing = REQUIRED_EPISODE_FIELDS - set(episode)
        if missing:
            raise ValueError(f"Episode {index} missing fields: {', '.join(sorted(missing))}")

        episode_number = episode["episode"]
        if not isinstance(episode_number, int) or isinstance(episode_number, bool):
            raise ValueError(f"Episode {index} episode must be an integer number")
        if episode_number != index:
            raise ValueError(f"Episode {index} field episode must equal {index}")

        for field in sorted(REQUIRED_EPISODE_FIELDS - {"episode"}):
            value = episode[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Episode {index} field {field} must be a non-empty string")

    normalized_pacing = _normalize_model_pacing(payload.get("episode_1_pacing"))
    pacing = validate_episode_pacing(normalized_pacing)
    validate_script_duration_capacity(episode_1_script, pacing["duration_seconds"])
    return {
        "script_plan": script_plan,
        "episode_1_script": episode_1_script.strip(),
        "episode_1_pacing": pacing,
        "pacing_profile_version": PACING_PROFILE_VERSION,
    }


def validate_episode_pacing(pacing):
    if not isinstance(pacing, dict):
        raise ValueError("pacing must be an object")

    duration = pacing.get("duration_seconds")
    if not isinstance(duration, int) or isinstance(duration, bool):
        raise ValueError("pacing duration_seconds must be an integer")
    if not EPISODE_DURATION_MIN_SECONDS <= duration <= EPISODE_DURATION_MAX_SECONDS:
        raise ValueError(
            "pacing duration_seconds must be between "
            f"{EPISODE_DURATION_MIN_SECONDS} and {EPISODE_DURATION_MAX_SECONDS}"
        )

    beats = pacing.get("beats")
    if not isinstance(beats, list) or len(beats) != len(REQUIRED_BEAT_TYPES):
        raise ValueError(f"pacing beats must contain exactly {len(REQUIRED_BEAT_TYPES)} entries")

    validated_beats = []
    for index, (beat, expected_type) in enumerate(zip(beats, REQUIRED_BEAT_TYPES), start=1):
        if not isinstance(beat, dict):
            raise ValueError(f"pacing beat {index} must be an object")
        if beat.get("beat_type") != expected_type:
            raise ValueError(f"pacing beat {index} must be {expected_type}")
        start = _required_int(beat.get("start_second"), f"pacing beat {expected_type} start_second")
        end = _required_int(beat.get("end_second"), f"pacing beat {expected_type} end_second")
        event = beat.get("event")
        if not isinstance(event, str) or not event.strip():
            raise ValueError(f"pacing beat {expected_type} event must be a non-empty string")
        if start < 0 or end <= start or end > duration:
            raise ValueError(f"pacing beat {expected_type} has an invalid time range")
        validated_beats.append(
            {
                "beat_type": expected_type,
                "start_second": start,
                "end_second": end,
                "event": event.strip(),
            }
        )

    for current, following in zip(validated_beats, validated_beats[1:]):
        if current["end_second"] != following["start_second"]:
            raise ValueError("pacing beats must form a continuous timeline")

    crisis, goal, obstacle_1, obstacle_2, resolution, next_crisis = validated_beats
    if crisis["start_second"] != 0 or crisis["end_second"] > 3:
        raise ValueError("crisis_open must start at 0 and finish within 3 seconds")
    if goal["end_second"] > duration // 3:
        raise ValueError("protagonist_goal must be established in the first third")
    for beat in (obstacle_1, obstacle_2, resolution):
        if beat["end_second"] - beat["start_second"] < 8:
            raise ValueError(
                f"pacing beat {beat['beat_type']} must have at least 8 seconds"
            )
    next_crisis_duration = next_crisis["end_second"] - next_crisis["start_second"]
    if next_crisis["end_second"] != duration or not 10 <= next_crisis_duration <= 30:
        raise ValueError("next_crisis must occupy the final 10 to 30 seconds")

    information_beats = pacing.get("information_beats")
    if not isinstance(information_beats, list) or not information_beats:
        raise ValueError("pacing information_beats must be a non-empty list")

    validated_information = []
    previous_at = None
    for index, item in enumerate(information_beats, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"information beat {index} must be an object")
        at_second = _required_int(item.get("at_second"), f"information beat {index} at_second")
        if not 0 <= at_second <= duration:
            raise ValueError(f"information beat {index} at_second is outside the episode")
        if previous_at is not None:
            if at_second <= previous_at:
                raise ValueError("information beat timestamps must increase")
            if at_second - previous_at > 15:
                raise ValueError("information beats must not be more than 15 seconds apart")
        information = item.get("information")
        consequence = item.get("consequence")
        for field, value in (("information", information), ("consequence", consequence)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"information beat {index} {field} must be a non-empty string")
        validated_information.append(
            {
                "at_second": at_second,
                "information": information.strip(),
                "consequence": consequence.strip(),
            }
        )
        previous_at = at_second

    if validated_information[0]["at_second"] > 3:
        raise ValueError("the first information beat must appear within 3 seconds")
    if duration - validated_information[-1]["at_second"] > 15:
        raise ValueError("the final information beat must be within 15 seconds of the ending")

    return {
        "duration_seconds": duration,
        "beats": validated_beats,
        "information_beats": validated_information,
        "profile_version": PACING_PROFILE_VERSION,
    }


def generate_episode_script(provider, outline, episode, previous_episode=None, next_episode=None):
    validated_outline = validate_outline(outline)
    if not isinstance(episode, dict):
        raise ValueError("Episode must be an object")

    missing = LEGACY_EPISODE_FIELDS - set(episode)
    if missing:
        raise ValueError(f"Episode missing fields: {', '.join(sorted(missing))}")
    episode_number = episode["episode"]
    if not isinstance(episode_number, int) or isinstance(episode_number, bool):
        raise ValueError("Episode number must be an integer")

    for field in sorted(LEGACY_EPISODE_FIELDS - {"episode"}):
        value = episode[field]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Episode field {field} must be a non-empty string")

    continuity = []
    if isinstance(previous_episode, dict):
        continuity.append(
            "上一集："
            f"{previous_episode.get('title', '')}；"
            f"摘要：{previous_episode.get('summary', '')}；"
            f"结尾悬念：{previous_episode.get('cliffhanger', '')}"
        )
    if isinstance(next_episode, dict):
        continuity.append(
            "下一集："
            f"{next_episode.get('title', '')}；"
            f"需要衔接的方向：{next_episode.get('summary', '')}"
        )

    messages = [
            {
                "role": "system",
                "content": (
                    "你是爆款 AI 漫画短剧编剧。只返回 JSON，不要返回 Markdown。"
                    'JSON 格式为 {"episode_script": "...", "pacing": '
                    '{"duration_seconds": 120, "beats": [...], "information_beats": [...]}}。'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"请创作第 {episode_number} 集完整剧本，并根据实际内容估算自然时长，"
                    f"{EPISODE_DURATION_MIN_SECONDS} 到 {EPISODE_DURATION_MAX_SECONDS} 秒仅为"
                    "安全边界，不设固定目标。先按每秒约3.5个汉字计算对白和旁白，"
                    "再为动作、反应、镜头停留、转场和情绪停顿额外预留至少25%时间；"
                    "质量优先，绝不能为了缩短时长压缩语速或只改时间标签。\n"
                    f"整部作品标题：{validated_outline['title']}\n"
                    f"核心设定：{validated_outline['core_premise']}\n"
                    f"主角：{validated_outline['protagonist']}\n"
                    f"长线梗概：{validated_outline['arc_summary']}\n"
                    f"本集标题：{episode['title']}\n"
                    f"本集摘要：{episode['summary']}\n"
                    f"核心冲突：{episode['key_conflict']}\n"
                    f"主角目标：{episode.get('episode_goal') or episode['summary']}\n"
                    f"第一次阻碍：{episode.get('obstacle_1') or episode['key_conflict']}\n"
                    f"第二次阻碍：{episode.get('obstacle_2') or episode['key_conflict']}\n"
                    f"解决或反转：{episode.get('resolution_or_reversal') or episode['summary']}\n"
                    f"下一集新危机：{episode.get('next_crisis') or episode['cliffhanger']}\n"
                    f"结尾悬念：{episode['cliffhanger']}\n"
                    + ("\n".join(continuity) if continuity else "")
                    + "\n按以下顺序生成连续的 pacing.beats：0–3秒 crisis_open，危机或结果前置；"
                    "随后尽早用 protagonist_goal 明确主角目标；再按内容需要依次安排 obstacle_1 "
                    "和 obstacle_2，第二次必须升级；之后安排 resolution_or_reversal；"
                    "最后10到30秒留给 next_crisis。除开场外不使用固定秒点，"
                    "每一段都应获得足以完整表演的时间。"
                    "每个 beat 包含 beat_type、start_second、end_second、event。"
                    "pacing.information_beats 每项包含 at_second、information、consequence；"
                    "开场 3 秒内必须有第一条，从开场到结尾任意信息空档不得超过 15 秒。"
                    "新信息必须改变观众认知、风险或角色行动，不能用情绪重复充数。"
                    "episode_script 必须包含带时间提示的场景、人物动作、对白和旁白，"
                    "并与前后集自然衔接。"
                ),
            },
    ]
    payload = provider.generate_json(messages, temperature=0.7)
    try:
        return _validate_generated_episode_payload(payload)
    except ScriptDurationCapacityError as exc:
        target_duration = min(exc.required_seconds, EPISODE_DURATION_MAX_SECONDS)
        if exc.required_seconds > EPISODE_DURATION_MAX_SECONDS:
            revision_instruction = (
                f"当前内容估算需要 {exc.required_seconds} 秒，超过 {EPISODE_DURATION_MAX_SECONDS} 秒安全上限。"
                "保留全部关键事件、两次阻碍、反转和结尾危机，只删除重复表达，"
                f"让自然表演时长接近 {target_duration} 秒。"
            )
        else:
            revision_instruction = (
                f"当前内容估算至少需要 {exc.required_seconds} 秒，不要删减剧情或压缩表演，"
                f"把自然总时长调整到至少 {target_duration} 秒。"
            )
        correction_messages = messages + [
            {
                "role": "assistant",
                "content": json.dumps(payload, ensure_ascii=False),
            },
            {
                "role": "user",
                "content": (
                    revision_instruction
                    + "请同步修正 episode_script 中的时间提示、pacing.duration_seconds、"
                    "全部连续 beats 和 information_beats，信息空档仍不得超过 15 秒。"
                    "只返回修正后的完整 JSON。"
                ),
            },
        ]
        corrected_payload = provider.generate_json(correction_messages, temperature=0.4)
        return _validate_generated_episode_payload(corrected_payload)


def _validate_generated_episode_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("Model response must be an object")
    episode_script = payload.get("episode_script")
    if not isinstance(episode_script, str) or not episode_script.strip():
        raise ValueError("Model response must include a non-empty episode_script")
    normalized_pacing = _normalize_model_pacing(payload.get("pacing"))
    pacing = validate_episode_pacing(normalized_pacing)
    try:
        validate_script_duration_capacity(episode_script, pacing["duration_seconds"])
    except ScriptDurationCapacityError as exc:
        if exc.required_seconds > EPISODE_DURATION_MAX_SECONDS:
            raise
        original_duration = pacing["duration_seconds"]
        pacing = _rescale_episode_pacing(pacing, exc.required_seconds)
        episode_script = _rescale_script_timestamps(episode_script, original_duration, exc.required_seconds)
    return {
        "episode_script": episode_script.strip(),
        "pacing": pacing,
        "pacing_profile_version": PACING_PROFILE_VERSION,
    }

def _normalize_model_pacing(pacing):
    if not isinstance(pacing, dict):
        return pacing
    duration = pacing.get("duration_seconds")
    beats = pacing.get("beats")
    if (
        not isinstance(duration, int)
        or isinstance(duration, bool)
        or not isinstance(beats, list)
        or len(beats) != len(REQUIRED_BEAT_TYPES)
        or not all(isinstance(beat, dict) for beat in beats)
    ):
        return pacing

    normalized = dict(pacing)
    normalized_beats = [dict(beat) for beat in beats]
    crisis = normalized_beats[0]
    goal = normalized_beats[1]
    crisis_start = crisis.get("start_second")
    crisis_end = crisis.get("end_second")
    goal_start = goal.get("start_second")
    if (
        all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in (crisis_start, crisis_end, goal_start)
        )
        and 0 <= crisis_start < crisis_end <= duration
        and goal_start == crisis_end
    ):
        corrected_end = min(crisis_end, 3)
        crisis["start_second"] = 0
        crisis["end_second"] = corrected_end
        goal["start_second"] = corrected_end

    resolution = normalized_beats[-2]
    next_crisis = normalized_beats[-1]
    start = next_crisis.get("start_second")
    end = next_crisis.get("end_second")
    resolution_start = resolution.get("start_second")
    if all(isinstance(value, int) and not isinstance(value, bool) for value in (start, end, resolution_start)):
        original_length = max(1, end - start)
        corrected_length = min(30, max(10, original_length))
        corrected_start = duration - corrected_length
        if corrected_start - resolution_start >= 8:
            resolution["end_second"] = corrected_start
            next_crisis["start_second"] = corrected_start
            next_crisis["end_second"] = duration
    normalized["beats"] = normalized_beats
    return normalized


def _rescale_episode_pacing(pacing, duration_seconds):
    original_duration = pacing["duration_seconds"]
    if duration_seconds <= original_duration:
        return pacing

    beats = pacing["beats"]
    crisis = dict(beats[0])
    next_crisis = dict(beats[-1])
    next_crisis_duration = next_crisis["end_second"] - next_crisis["start_second"]
    new_next_crisis_start = duration_seconds - next_crisis_duration
    original_middle_start = crisis["end_second"]
    original_middle_end = next_crisis["start_second"]
    original_middle_span = original_middle_end - original_middle_start
    new_middle_span = new_next_crisis_start - original_middle_start
    if original_middle_span <= 0 or new_middle_span <= 0:
        raise ValueError("pacing beats cannot be expanded")

    scaled_beats = [crisis]
    previous_end = crisis["end_second"]
    for index, beat in enumerate(beats[1:-1], start=1):
        if index == len(beats) - 2:
            scaled_end = new_next_crisis_start
        else:
            position = (beat["end_second"] - original_middle_start) / original_middle_span
            scaled_end = round(original_middle_start + position * new_middle_span)
        scaled_beat = dict(beat)
        scaled_beat["start_second"] = previous_end
        scaled_beat["end_second"] = scaled_end
        scaled_beats.append(scaled_beat)
        previous_end = scaled_end

    next_crisis["start_second"] = new_next_crisis_start
    next_crisis["end_second"] = duration_seconds
    scaled_beats.append(next_crisis)

    information_beats = pacing["information_beats"]
    if len(information_beats) < 2:
        raise ValueError("pacing needs more information beats before it can be expanded")
    first_at = information_beats[0]["at_second"]
    original_tail_gap = original_duration - information_beats[-1]["at_second"]
    final_at = duration_seconds - min(15, original_tail_gap)
    scaled_source = []
    for index, item in enumerate(information_beats):
        scaled_item = dict(item)
        scaled_item["at_second"] = round(
            first_at
            + (final_at - first_at) * index / (len(information_beats) - 1)
        )
        scaled_source.append(scaled_item)

    scaled_information = [scaled_source[0]]
    for left, right in zip(scaled_source, scaled_source[1:]):
        gap = right["at_second"] - left["at_second"]
        segments = max(1, math.ceil(gap / 15))
        for step in range(1, segments):
            ratio = step / segments
            scaled_information.append(
                {
                    "at_second": round(
                        left["at_second"] + gap * ratio
                    ),
                    "information": (
                        f"{left['information']}之后，{right['information']}逐步显现"
                    ),
                    "consequence": (
                        f"{left['consequence']}；并推动：{right['consequence']}"
                    ),
                }
            )
        scaled_information.append(right)

    scaled_pacing = {
        "duration_seconds": duration_seconds,
        "beats": scaled_beats,
        "information_beats": scaled_information,
        "profile_version": PACING_PROFILE_VERSION,
    }
    return validate_episode_pacing(scaled_pacing)


def _rescale_script_timestamps(episode_script, original_duration, duration_seconds):
    if duration_seconds <= original_duration:
        return episode_script

    def replace_time_range(match):
        start = int(match.group("start"))
        end = int(match.group("end"))
        scaled_start = start if start <= 3 else round(start * duration_seconds / original_duration)
        scaled_end = end if end <= 3 else round(end * duration_seconds / original_duration)
        return (
            f"{scaled_start}{match.group('separator')}{scaled_end}"
            f"{match.group('seconds')}"
        )

    return re.sub(
        r"(?P<start>\d+)\s*(?P<separator>[—–-])\s*(?P<end>\d+)\s*(?P<seconds>秒)",
        replace_time_range,
        episode_script,
    )



def estimate_spoken_duration_seconds(episode_script):
    spoken_characters = 0
    spoken_lines = 0
    for raw_line in str(episode_script or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("【", "[")):
            continue
        match = re.match(r"^([^：:\n]{1,24})[：:]\s*(.+)$", line)
        if not match:
            continue
        speaker = re.sub(r"[（(].*?[）)]", "", match.group(1)).strip()
        if speaker.startswith(NON_SPOKEN_LINE_PREFIXES):
            continue
        character_count = sum(character.isalnum() for character in match.group(2))
        if character_count:
            spoken_characters += character_count
            spoken_lines += 1
    if not spoken_characters:
        return 0
    return math.ceil(
        spoken_characters / SPOKEN_CHARACTERS_PER_SECOND
        + spoken_lines * SPEAKER_PAUSE_SECONDS
        + BASE_ACTION_ALLOWANCE_SECONDS
    )


def validate_script_duration_capacity(episode_script, duration_seconds):
    required_seconds = estimate_spoken_duration_seconds(episode_script)
    tolerance_seconds = max(
        DURATION_ESTIMATE_TOLERANCE_SECONDS,
        math.ceil(required_seconds * DURATION_ESTIMATE_TOLERANCE_RATIO),
    )
    if required_seconds and duration_seconds + tolerance_seconds < required_seconds:
        raise ScriptDurationCapacityError(required_seconds, duration_seconds)
    return required_seconds


def _required_int(value, field_name):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    return value
