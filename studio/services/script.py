import json
import math
import re

from studio.constants import (
    COLD_OPEN_DURATION_SECONDS,
    COLD_OPEN_SOURCE_MIN_POSITION_RATIO,
    EPISODE_COUNT,
    EPISODE_DURATION_MAX_SECONDS,
    EPISODE_DURATION_MIN_SECONDS,
    EPISODE_DURATION_TARGET_SECONDS,
    LEGACY_EPISODE_DURATION_MAX_SECONDS,
    LEGACY_MAX_INFORMATION_GAP_SECONDS,
    LEGACY_NEXT_CRISIS_MAX_SECONDS,
    LEGACY_NEXT_CRISIS_MIN_SECONDS,
    LEGACY_PACING_PROFILE_VERSION,
    MAX_INFORMATION_GAP_SECONDS,
    NEXT_CRISIS_MAX_SECONDS,
    NEXT_CRISIS_MIN_SECONDS,
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
EXPECTED_BEAT_IDS = {
    beat_type: f"beat_{index:02d}_{beat_type}"
    for index, beat_type in enumerate(REQUIRED_BEAT_TYPES, start=1)
}
REQUIRED_COLD_OPEN_FIELDS = {
    "hook_type",
    "source_beat_id",
    "duration_seconds",
    "withheld_reveal",
    "return_bridge",
}
REQUIRED_CONTINUITY_FIELDS = {"carry_in", "state_delta", "carry_out"}
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


class EpisodeDurationRangeError(ValueError):
    def __init__(self, duration_seconds, minimum_seconds, maximum_seconds):
        self.duration_seconds = duration_seconds
        self.minimum_seconds = minimum_seconds
        self.maximum_seconds = maximum_seconds
        super().__init__(
            "pacing duration_seconds must be between "
            f"{minimum_seconds} and {maximum_seconds}"
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
    messages = [
        {
            "role": "system",
            "content": (
                "你是爆款 AI 漫画短剧编剧。只返回 JSON，不要返回 Markdown。"
                "所有剧情必须先按硬时长预算设计，再扩写剧本。"
                'JSON 格式为 {"script_plan": [...], "episode_1_script": "...", '
                '"episode_1_pacing": {"duration_seconds": 75, "cold_open": {...}, '
                '"beats": [...], "information_beats": [...]}, '
                '"episode_1_continuity": {"carry_in": {}, "state_delta": {}, '
                '"carry_out": {}}}。'
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
                f"固定规格：共 {EPISODE_COUNT} 集；每集必须在 "
                f"{EPISODE_DURATION_MIN_SECONDS} 到 {EPISODE_DURATION_MAX_SECONDS} 秒内，"
                f"默认按 {EPISODE_DURATION_TARGET_SECONDS} 秒创作。\n"
                f"script_plan 必须包含 {EPISODE_COUNT} 条，每条都必须包含字段："
                f"{REQUIRED_EPISODE_FIELD_LIST}。\n"
                f"episode 字段必须按 1 到 {EPISODE_COUNT} 递增，且必须是整数。"
                "每集只允许一个目标、一条主冲突链和一次核心反转，主要角色不超过3人、"
                "场景不超过2个；分别写明两次递进阻碍、反转兑现和下集新问题。\n"
                "按每秒约 3.5 个汉字估算对白与旁白，再为动作、镜头停留、转场和情绪"
                "停顿预留时间。若内容超出硬上限，删除重复说明、合并同功能事件并用画面"
                "替代解释，不得扩展时间轴、压缩语速或只改时间标签。\n"
                f"episode_1_pacing 的 crisis_open 必须固定为 0–{COLD_OPEN_DURATION_SECONDS}秒。"
                "cold_open 必须包含 hook_type、source_beat_id、duration_seconds、"
                "withheld_reveal、return_bridge；duration_seconds 必须为3，source_beat_id"
                "必须引用本集后40%的真实 beat，且不能引用 next_crisis。片花只能隐去答案"
                "后提出问题，return_bridge 负责自然回切正片。\n"
                "beats 按 crisis_open、protagonist_goal、obstacle_1、obstacle_2、"
                "resolution_or_reversal、next_crisis 排列。每项必须包含固定 beat_id、"
                "beat_type、start_second、end_second、event；固定 beat_id 依次为 "
                + "、".join(EXPECTED_BEAT_IDS[beat_type] for beat_type in REQUIRED_BEAT_TYPES)
                + "。"
                f"最后{NEXT_CRISIS_MIN_SECONDS}到{NEXT_CRISIS_MAX_SECONDS}秒用于 next_crisis。\n"
                "information_beats 每项包含 at_second、information、consequence；"
                f"任意相邻信息点及首尾空档不得超过 {MAX_INFORMATION_GAP_SECONDS} 秒。\n"
                "episode_1_continuity 必须包含 carry_in、state_delta、carry_out 三个 JSON"
                "对象；第1集 carry_in 必须为空对象，state_delta 只写本集实际变化，"
                "carry_out 写本集结束后的完整可延续状态。episode_1_script 必须包含场景、"
                "人物动作、对白和旁白，并严格对应节拍表。"
            ),
        },
    ]
    payload = provider.generate_json(messages, temperature=0.7)
    try:
        return validate_script_payload(payload)
    except (ScriptDurationCapacityError, EpisodeDurationRangeError) as exc:
        if not _is_compressible_duration_error(exc):
            raise
        corrected_payload = provider.generate_json(
            _compression_messages(messages, payload, exc),
            temperature=0.4,
        )
        return validate_script_payload(corrected_payload)


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
    continuity = validate_continuity_payload(
        payload.get("episode_1_continuity"),
        expected_carry_in={},
    )
    return {
        "script_plan": script_plan,
        "episode_1_script": episode_1_script.strip(),
        "episode_1_pacing": pacing,
        "episode_1_continuity": continuity,
        "pacing_profile_version": PACING_PROFILE_VERSION,
    }


def validate_continuity_payload(continuity, expected_carry_in=None):
    if not isinstance(continuity, dict):
        raise ValueError("continuity must be an object")
    missing = REQUIRED_CONTINUITY_FIELDS - set(continuity)
    if missing:
        raise ValueError(
            f"continuity missing fields: {', '.join(sorted(missing))}"
        )

    validated = {}
    for field in sorted(REQUIRED_CONTINUITY_FIELDS):
        value = continuity[field]
        if not isinstance(value, dict):
            raise ValueError(f"continuity {field} must be an object")
        validated[field] = dict(value)

    if expected_carry_in is not None:
        if not isinstance(expected_carry_in, dict):
            raise ValueError("expected carry_in must be an object")
        if validated["carry_in"] != expected_carry_in:
            raise ValueError("continuity carry_in must equal the previous carry_out")
    return validated


def validate_episode_pacing(pacing):
    if not isinstance(pacing, dict):
        raise ValueError("pacing must be an object")

    profile_version = pacing.get("profile_version")
    if profile_version == LEGACY_PACING_PROFILE_VERSION:
        return _validate_legacy_episode_pacing(pacing)
    if profile_version not in (None, PACING_PROFILE_VERSION):
        raise ValueError(f"Unsupported pacing profile_version: {profile_version}")

    duration = pacing.get("duration_seconds")
    if not isinstance(duration, int) or isinstance(duration, bool):
        raise ValueError("pacing duration_seconds must be an integer")
    if not EPISODE_DURATION_MIN_SECONDS <= duration <= EPISODE_DURATION_MAX_SECONDS:
        raise EpisodeDurationRangeError(
            duration,
            EPISODE_DURATION_MIN_SECONDS,
            EPISODE_DURATION_MAX_SECONDS,
        )

    validated_beats = _validate_beats(pacing.get("beats"), duration, require_stable_ids=True)

    crisis, goal, obstacle_1, obstacle_2, resolution, next_crisis = validated_beats
    if (
        crisis["start_second"] != 0
        or crisis["end_second"] != COLD_OPEN_DURATION_SECONDS
    ):
        raise ValueError(
            "crisis_open must occupy exactly the first "
            f"{COLD_OPEN_DURATION_SECONDS} seconds"
        )
    if goal["end_second"] > duration // 3:
        raise ValueError("protagonist_goal must be established in the first third")
    for beat in (obstacle_1, obstacle_2, resolution):
        if beat["end_second"] - beat["start_second"] < 8:
            raise ValueError(
                f"pacing beat {beat['beat_type']} must have at least 8 seconds"
            )
    next_crisis_duration = next_crisis["end_second"] - next_crisis["start_second"]
    if (
        next_crisis["end_second"] != duration
        or not NEXT_CRISIS_MIN_SECONDS
        <= next_crisis_duration
        <= NEXT_CRISIS_MAX_SECONDS
    ):
        raise ValueError(
            "next_crisis must occupy the final "
            f"{NEXT_CRISIS_MIN_SECONDS} to {NEXT_CRISIS_MAX_SECONDS} seconds"
        )

    cold_open = _validate_cold_open(pacing.get("cold_open"), validated_beats, duration)
    validated_information = _validate_information_beats(
        pacing.get("information_beats"),
        duration,
        MAX_INFORMATION_GAP_SECONDS,
    )

    return {
        "duration_seconds": duration,
        "cold_open": cold_open,
        "beats": validated_beats,
        "information_beats": validated_information,
        "profile_version": PACING_PROFILE_VERSION,
    }


def _validate_legacy_episode_pacing(pacing):
    duration = pacing.get("duration_seconds")
    if not isinstance(duration, int) or isinstance(duration, bool):
        raise ValueError("pacing duration_seconds must be an integer")
    if not EPISODE_DURATION_MIN_SECONDS <= duration <= LEGACY_EPISODE_DURATION_MAX_SECONDS:
        raise EpisodeDurationRangeError(
            duration,
            EPISODE_DURATION_MIN_SECONDS,
            LEGACY_EPISODE_DURATION_MAX_SECONDS,
        )

    validated_beats = _validate_beats(
        pacing.get("beats"),
        duration,
        require_stable_ids=False,
    )
    crisis, goal, obstacle_1, obstacle_2, resolution, next_crisis = validated_beats
    if crisis["start_second"] != 0 or crisis["end_second"] > COLD_OPEN_DURATION_SECONDS:
        raise ValueError("crisis_open must start at 0 and finish within 3 seconds")
    if goal["end_second"] > duration // 3:
        raise ValueError("protagonist_goal must be established in the first third")
    for beat in (obstacle_1, obstacle_2, resolution):
        if beat["end_second"] - beat["start_second"] < 8:
            raise ValueError(
                f"pacing beat {beat['beat_type']} must have at least 8 seconds"
            )
    next_crisis_duration = next_crisis["end_second"] - next_crisis["start_second"]
    if (
        next_crisis["end_second"] != duration
        or not LEGACY_NEXT_CRISIS_MIN_SECONDS
        <= next_crisis_duration
        <= LEGACY_NEXT_CRISIS_MAX_SECONDS
    ):
        raise ValueError("next_crisis must occupy the final 10 to 30 seconds")

    result = {
        "duration_seconds": duration,
        "beats": validated_beats,
        "information_beats": _validate_information_beats(
            pacing.get("information_beats"),
            duration,
            LEGACY_MAX_INFORMATION_GAP_SECONDS,
        ),
        "profile_version": LEGACY_PACING_PROFILE_VERSION,
    }
    if isinstance(pacing.get("cold_open"), dict):
        result["cold_open"] = dict(pacing["cold_open"])
    return result


def _validate_beats(beats, duration, require_stable_ids):
    if not isinstance(beats, list) or len(beats) != len(REQUIRED_BEAT_TYPES):
        raise ValueError(
            f"pacing beats must contain exactly {len(REQUIRED_BEAT_TYPES)} entries"
        )

    validated_beats = []
    for index, (beat, expected_type) in enumerate(
        zip(beats, REQUIRED_BEAT_TYPES),
        start=1,
    ):
        if not isinstance(beat, dict):
            raise ValueError(f"pacing beat {index} must be an object")
        if beat.get("beat_type") != expected_type:
            raise ValueError(f"pacing beat {index} must be {expected_type}")
        expected_id = EXPECTED_BEAT_IDS[expected_type]
        beat_id = beat.get("beat_id")
        if require_stable_ids and beat_id != expected_id:
            raise ValueError(f"pacing beat {expected_type} beat_id must be {expected_id}")
        if beat_id is not None and (not isinstance(beat_id, str) or not beat_id.strip()):
            raise ValueError(f"pacing beat {expected_type} beat_id must be a non-empty string")
        start = _required_int(
            beat.get("start_second"),
            f"pacing beat {expected_type} start_second",
        )
        end = _required_int(
            beat.get("end_second"),
            f"pacing beat {expected_type} end_second",
        )
        event = beat.get("event")
        if not isinstance(event, str) or not event.strip():
            raise ValueError(
                f"pacing beat {expected_type} event must be a non-empty string"
            )
        if start < 0 or end <= start or end > duration:
            raise ValueError(f"pacing beat {expected_type} has an invalid time range")
        validated = {
            "beat_type": expected_type,
            "start_second": start,
            "end_second": end,
            "event": event.strip(),
        }
        if beat_id is not None:
            validated["beat_id"] = beat_id.strip()
        validated_beats.append(validated)

    for current, following in zip(validated_beats, validated_beats[1:]):
        if current["end_second"] != following["start_second"]:
            raise ValueError("pacing beats must form a continuous timeline")
    return validated_beats


def _validate_cold_open(cold_open, beats, duration):
    if not isinstance(cold_open, dict):
        raise ValueError("pacing cold_open must be an object")
    missing = REQUIRED_COLD_OPEN_FIELDS - set(cold_open)
    if missing:
        raise ValueError(
            f"pacing cold_open missing fields: {', '.join(sorted(missing))}"
        )

    duration_seconds = _required_int(
        cold_open.get("duration_seconds"),
        "pacing cold_open duration_seconds",
    )
    if duration_seconds != COLD_OPEN_DURATION_SECONDS:
        raise ValueError(
            f"pacing cold_open duration_seconds must be {COLD_OPEN_DURATION_SECONDS}"
        )

    validated = {"duration_seconds": duration_seconds}
    for field in (
        "hook_type",
        "source_beat_id",
        "withheld_reveal",
        "return_bridge",
    ):
        value = cold_open.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"pacing cold_open {field} must be a non-empty string")
        validated[field] = value.strip()

    source = next(
        (beat for beat in beats if beat.get("beat_id") == validated["source_beat_id"]),
        None,
    )
    if source is None:
        raise ValueError("pacing cold_open source_beat_id must reference a real beat_id")
    if source["beat_type"] in {"crisis_open", "next_crisis"}:
        raise ValueError("pacing cold_open cannot source crisis_open or next_crisis")
    if source["start_second"] < math.ceil(
        duration * COLD_OPEN_SOURCE_MIN_POSITION_RATIO
    ):
        raise ValueError("pacing cold_open source must begin in the final 40% of the episode")
    return validated


def _validate_information_beats(information_beats, duration, maximum_gap_seconds):
    if not isinstance(information_beats, list) or not information_beats:
        raise ValueError("pacing information_beats must be a non-empty list")

    validated_information = []
    previous_at = None
    for index, item in enumerate(information_beats, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"information beat {index} must be an object")
        at_second = _required_int(
            item.get("at_second"),
            f"information beat {index} at_second",
        )
        if not 0 <= at_second <= duration:
            raise ValueError(f"information beat {index} at_second is outside the episode")
        if previous_at is not None:
            if at_second <= previous_at:
                raise ValueError("information beat timestamps must increase")
            if at_second - previous_at > maximum_gap_seconds:
                raise ValueError(
                    "information beats must not be more than "
                    f"{maximum_gap_seconds} seconds apart"
                )
        information = item.get("information")
        consequence = item.get("consequence")
        for field, value in (("information", information), ("consequence", consequence)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"information beat {index} {field} must be a non-empty string"
                )
        validated_information.append(
            {
                "at_second": at_second,
                "information": information.strip(),
                "consequence": consequence.strip(),
            }
        )
        previous_at = at_second

    if validated_information[0]["at_second"] > COLD_OPEN_DURATION_SECONDS:
        raise ValueError("the first information beat must appear within 3 seconds")
    if duration - validated_information[-1]["at_second"] > maximum_gap_seconds:
        raise ValueError(
            "the final information beat must be within "
            f"{maximum_gap_seconds} seconds of the ending"
        )
    return validated_information


def generate_episode_script(
    provider,
    outline,
    episode,
    previous_episode=None,
    next_episode=None,
    continuity_context=None,
):
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

    expected_carry_in = _resolve_expected_carry_in(
        continuity_context,
        previous_episode,
    )
    continuity_instruction = (
        "没有可用的上集状态账本；根据给定摘要建立 carry_in。"
        if expected_carry_in is None
        else (
            "carry_in 必须逐项等于以下上集实际 carry_out，不得自行改写："
            + json.dumps(expected_carry_in, ensure_ascii=False, sort_keys=True)
        )
    )

    messages = [
            {
                "role": "system",
                "content": (
                    "你是爆款 AI 漫画短剧编剧。只返回 JSON，不要返回 Markdown。"
                    'JSON 格式为 {"episode_script": "...", "pacing": '
                    '{"duration_seconds": 75, "cold_open": {...}, "beats": [...], '
                    '"information_beats": [...]}, "continuity": '
                    '{"carry_in": {}, "state_delta": {}, "carry_out": {}}}。'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"请创作第 {episode_number} 集完整剧本。时长必须在 "
                    f"{EPISODE_DURATION_MIN_SECONDS} 到 {EPISODE_DURATION_MAX_SECONDS} 秒内，"
                    f"默认按 {EPISODE_DURATION_TARGET_SECONDS} 秒设计。先按每秒约3.5个汉字"
                    "计算对白和旁白，并为动作、反应、镜头停留、转场和情绪停顿预留时间；"
                    "超出预算时删除重复解释、合并同功能事件、用画面替代说明，不得扩展"
                    "时间轴、压缩语速或只改时间标签。\n"
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
                    + "\n本集只保留一个目标、一条主冲突链和一次核心反转，主要角色不超过3人、"
                    "场景不超过2个。按顺序生成连续 pacing.beats：0–3秒 crisis_open；"
                    "protagonist_goal；递进的 obstacle_1 和 obstacle_2；"
                    "resolution_or_reversal 必须兑现本集冲突；"
                    f"最后{NEXT_CRISIS_MIN_SECONDS}到{NEXT_CRISIS_MAX_SECONDS}秒才进入 next_crisis。"
                    "每个 beat 包含 beat_id、beat_type、start_second、end_second、event；"
                    "beat_id 固定依次为 "
                    + "、".join(EXPECTED_BEAT_IDS[beat_type] for beat_type in REQUIRED_BEAT_TYPES)
                    + "。pacing.cold_open 必须包含 hook_type、source_beat_id、duration_seconds、"
                    "withheld_reveal、return_bridge；duration_seconds 固定为3，source_beat_id"
                    "必须引用本集后40%的真实 beat_id 且不得引用 next_crisis。片花内容必须"
                    "在来源 beat 原样兑现，withheld_reveal 写明刻意隐去的答案，return_bridge"
                    "负责自然回到正片。"
                    "pacing.information_beats 每项包含 at_second、information、consequence；"
                    f"开场3秒内必须有第一条，任意信息空档不得超过 {MAX_INFORMATION_GAP_SECONDS} 秒。"
                    "新信息必须改变观众认知、风险或角色行动，不能用情绪重复充数。"
                    "continuity 必须包含 carry_in、state_delta、carry_out 三个 JSON 对象。"
                    + continuity_instruction
                    + "state_delta 只记录本集真正发生的状态变化；carry_out 是应用变化后的"
                    "完整状态，必须覆盖人物目标、知识、关系、地点、伤势、道具和未决伏笔中"
                    "本集涉及的项目。"
                    "episode_script 必须包含带时间提示的场景、人物动作、对白和旁白，"
                    "并与前后集自然衔接。"
                ),
            },
    ]
    payload = provider.generate_json(messages, temperature=0.7)
    try:
        return _validate_generated_episode_payload(payload, expected_carry_in)
    except (ScriptDurationCapacityError, EpisodeDurationRangeError) as exc:
        if not _is_compressible_duration_error(exc):
            raise
        corrected_payload = provider.generate_json(
            _compression_messages(messages, payload, exc),
            temperature=0.4,
        )
        return _validate_generated_episode_payload(
            corrected_payload,
            expected_carry_in,
        )


def _validate_generated_episode_payload(payload, expected_carry_in=None):
    if not isinstance(payload, dict):
        raise ValueError("Model response must be an object")
    episode_script = payload.get("episode_script")
    if not isinstance(episode_script, str) or not episode_script.strip():
        raise ValueError("Model response must include a non-empty episode_script")
    normalized_pacing = _normalize_model_pacing(payload.get("pacing"))
    pacing = validate_episode_pacing(normalized_pacing)
    validate_script_duration_capacity(episode_script, pacing["duration_seconds"])
    continuity = validate_continuity_payload(
        payload.get("continuity"),
        expected_carry_in=expected_carry_in,
    )
    return {
        "episode_script": episode_script.strip(),
        "pacing": pacing,
        "continuity": continuity,
        "pacing_profile_version": PACING_PROFILE_VERSION,
    }


def _resolve_expected_carry_in(continuity_context, previous_episode):
    if continuity_context is not None:
        if not isinstance(continuity_context, dict):
            raise ValueError("continuity_context must be an object")
        carry_out = continuity_context.get("carry_out")
        if carry_out is not None:
            if not isinstance(carry_out, dict):
                raise ValueError("continuity_context carry_out must be an object")
            return dict(carry_out)
        return dict(continuity_context)

    if isinstance(previous_episode, dict):
        for field in ("continuity", "continuity_payload"):
            previous_continuity = previous_episode.get(field)
            if not isinstance(previous_continuity, dict):
                continue
            carry_out = previous_continuity.get("carry_out")
            if isinstance(carry_out, dict):
                return dict(carry_out)
    return None


def _is_compressible_duration_error(exc):
    if isinstance(exc, ScriptDurationCapacityError):
        return True
    return (
        isinstance(exc, EpisodeDurationRangeError)
        and exc.duration_seconds > EPISODE_DURATION_MAX_SECONDS
    )


def _compression_messages(messages, payload, exc):
    if isinstance(exc, ScriptDurationCapacityError):
        overrun = f"对白与停顿估算需要 {exc.required_seconds} 秒"
    else:
        overrun = f"模型给出的时间轴为 {exc.duration_seconds} 秒"
    return messages + [
        {
            "role": "assistant",
            "content": json.dumps(payload, ensure_ascii=False),
        },
        {
            "role": "user",
            "content": (
                f"上一个版本超出时长预算：{overrun}。这是唯一一次压缩修订。"
                f"把成片严格压缩到 {EPISODE_DURATION_MIN_SECONDS}–"
                f"{EPISODE_DURATION_MAX_SECONDS} 秒，优先接近 "
                f"{EPISODE_DURATION_TARGET_SECONDS} 秒。删除重复说明、重复情绪和不改变"
                "局势的对白，合并同功能事件，并把可视化的信息改用动作表达；不得扩展"
                "时间轴、加快语速或只修改时间标签。保留一个目标、两次递进阻碍、一次"
                "反转兑现、真实来源的3秒片花和5–8秒尾钩。同步重写完整剧本、pacing、"
                f"information_beats（空档不得超过 {MAX_INFORMATION_GAP_SECONDS} 秒）和"
                "continuity，只返回修正后的完整 JSON。"
            ),
        },
    ]

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

    is_legacy = pacing.get("profile_version") == LEGACY_PACING_PROFILE_VERSION
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
        corrected_end = (
            min(crisis_end, COLD_OPEN_DURATION_SECONDS)
            if is_legacy
            else COLD_OPEN_DURATION_SECONDS
        )
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
        minimum_length = (
            LEGACY_NEXT_CRISIS_MIN_SECONDS
            if is_legacy
            else NEXT_CRISIS_MIN_SECONDS
        )
        maximum_length = (
            LEGACY_NEXT_CRISIS_MAX_SECONDS
            if is_legacy
            else NEXT_CRISIS_MAX_SECONDS
        )
        corrected_length = min(maximum_length, max(minimum_length, original_length))
        corrected_start = duration - corrected_length
        if corrected_start - resolution_start >= 8:
            resolution["end_second"] = corrected_start
            next_crisis["start_second"] = corrected_start
            next_crisis["end_second"] = duration
    normalized["beats"] = normalized_beats
    return normalized


def _rescale_episode_pacing(pacing, duration_seconds):
    """Expand a persisted content_adaptive_v2 timeline for legacy callers only."""
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
    final_at = duration_seconds - min(
        LEGACY_MAX_INFORMATION_GAP_SECONDS,
        original_tail_gap,
    )
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
        segments = max(
            1,
            math.ceil(gap / LEGACY_MAX_INFORMATION_GAP_SECONDS),
        )
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
        "profile_version": LEGACY_PACING_PROFILE_VERSION,
    }
    return validate_episode_pacing(scaled_pacing)


def _rescale_script_timestamps(episode_script, original_duration, duration_seconds):
    """Expand timestamp labels for persisted content_adaptive_v2 scripts only."""
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
