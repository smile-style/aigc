import json
import re

from studio.constants import (
    COLD_OPEN_DURATION_SECONDS,
    EPISODE_DURATION_MAX_SECONDS,
    EPISODE_DURATION_MIN_SECONDS,
    EPISODE_DURATION_TARGET_SECONDS,
    LEGACY_EPISODE_DURATION_MAX_SECONDS,
    LEGACY_PACING_PROFILE_VERSION,
    MAX_STORYBOARD_SHOTS,
    MIN_STORYBOARD_SHOTS,
)


REQUIRED_STORYBOARD_FIELDS = {
    "shot_number",
    "duration",
    "visual_description",
    "character_action",
    "dialogue_or_narration",
    "camera_language",
    "image_prompt",
    "video_prompt",
}
REQUIRED_STORYBOARD_FIELD_LIST = ", ".join(sorted(REQUIRED_STORYBOARD_FIELDS))
REQUIRED_STORYBOARD_TEXT_FIELDS = REQUIRED_STORYBOARD_FIELDS - {"shot_number"}
MIN_SHOT_DURATION_SECONDS = 4
MAX_SHOT_DURATION_SECONDS = 15


def generate_storyboard(
    provider,
    episode_script,
    episode_number=1,
    pacing=None,
    *,
    include_metadata=False,
):
    if not isinstance(episode_script, str) or not episode_script.strip():
        raise ValueError("episode_script must be a non-empty string")

    expected_duration = _pacing_duration(pacing)
    maximum_duration = (
        LEGACY_EPISODE_DURATION_MAX_SECONDS
        if isinstance(pacing, dict)
        and pacing.get("profile_version") == LEGACY_PACING_PROFILE_VERSION
        else EPISODE_DURATION_MAX_SECONDS
    )
    pacing_cold_open = _pacing_cold_open(pacing)
    body_beat_ids = _body_beat_ids(pacing) if pacing_cold_open else []
    body_duration = expected_duration - (
        pacing_cold_open["duration_seconds"] if pacing_cold_open else 0
    )
    pacing_context = ""
    if pacing:
        pacing_context = (
            "\n必须逐段继承以下已审核剧情节拍，不得改变事件顺序或信息出现时间：\n"
            + json.dumps(pacing, ensure_ascii=False)
        )

    payload = provider.generate_json(
        [
            {
                "role": "system",
                "content": (
                    "你是 AI 短剧分镜设计师。只返回 JSON，不要返回 Markdown 或额外说明。"
                    'JSON 格式为 {"storyboard_prompts": [...], "cold_open": {...}}。'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"请将下面这个第 {episode_number} 集剧本拆解成适合 AI 漫画和视频生成的分镜。\n"
                    f"成片总时长为 {expected_duration} 秒。"
                    f"{EPISODE_DURATION_MIN_SECONDS} 到 {maximum_duration} 秒仅是"
                    "安全边界；分镜必须忠实承接该估时，不得通过压缩台词或删减表演来缩短。\n"
                    f"分镜数量必须在 {MIN_STORYBOARD_SHOTS} 到 {MAX_STORYBOARD_SHOTS} 之间。\n"
                    "storyboard_prompts 中的每个分镜都必须包含字段："
                    f"{REQUIRED_STORYBOARD_FIELD_LIST}。\n"
                    "dialogue_or_narration 只填写人物实际说出口的台词，并用“人物名：台词”标明说话人；"
                    "无人说话时固定填写“无对白”，不要填写旁白、音效、系统提示、画面字幕、动作或环境描述。\n"
                    "shot_number 尽量使用整数；如果输出成字符串，也必须能明确解析为顺序编号。\n"
                    "所有文本字段都必须是非空字符串，内容要具体、可直接用于图像和视频生成。\n"
                    "另外输出 character_names 字符串数组和 duration_seconds 整数；"
                    f"character_names 只填写本镜头实际出场的角色姓名，duration_seconds 范围为 "
                    f"{MIN_SHOT_DURATION_SECONDS} 到 {MAX_SHOT_DURATION_SECONDS}。\n"
                    f"正片分镜的 duration_seconds 相加必须恰好等于 {body_duration} 秒，"
                    "并让危机、目标、两次阻碍、解决或反转、下集危机保持原有顺序和时间位置。"
                    + (
                        "\n这是 short_drama_v3 分镜。每个分镜还必须输出非空 beat_id，"
                        "并原样使用 pacing 中的稳定 beat_id。不要把 3 秒片花做成额外分镜。"
                        "正片分镜从 3 秒后的回切开始，不得再使用 beat_01_crisis_open；"
                        f"只允许使用这些正片 beat_id：{', '.join(body_beat_ids)}。"
                        f"第一个正片镜头必须先落实 return_bridge“{pacing_cold_open.get('return_bridge', '')}”，"
                        "再继续主角目标，保证片花回切后剧情连贯。"
                        "cold_open 必须输出 source_beat_id、source_shot_number、"
                        "trim_start_ms、trim_end_ms；source_shot_number 必须指向 source_beat_id "
                        "对应的正片分镜，裁剪区间必须恰好 3000ms。该镜头稍后会被复用："
                        "先裁剪为片花播放，再在正片位置完整播放。"
                        if pacing_cold_open
                        else ""
                    )
                    + f"{pacing_context}\n"
                    f"第 {episode_number} 集剧本如下：\n{episode_script}"
                ),
            },
        ],
        temperature=0.6,
    )
    prompts = validate_storyboard_payload(
        payload,
        expected_duration_seconds=body_duration,
        require_beat_ids=bool(pacing_cold_open),
        allowed_beat_ids=body_beat_ids or None,
        required_beat_ids=body_beat_ids or None,
    )
    cold_open = resolve_cold_open_payload(
        payload,
        prompts,
        pacing_cold_open,
    )
    if include_metadata:
        return {
            "storyboard_prompts": prompts,
            "cold_open": cold_open,
        }
    return prompts


def validate_storyboard_payload(
    payload,
    expected_duration_seconds=None,
    *,
    require_beat_ids=False,
    allowed_beat_ids=None,
    required_beat_ids=None,
):
    if not isinstance(payload, dict):
        raise ValueError("Model response must be an object")

    storyboard_prompts = payload.get("storyboard_prompts")
    if not isinstance(storyboard_prompts, list):
        raise ValueError("Model response must include a storyboard_prompts list")

    prompt_count = len(storyboard_prompts)
    if prompt_count < MIN_STORYBOARD_SHOTS or prompt_count > MAX_STORYBOARD_SHOTS:
        raise ValueError(
            "Expected storyboard_prompts count between "
            f"{MIN_STORYBOARD_SHOTS} and {MAX_STORYBOARD_SHOTS}, got {prompt_count}"
        )

    total_duration = 0
    observed_beat_ids = set()
    allowed_beat_ids = set(allowed_beat_ids or [])
    for index, shot in enumerate(storyboard_prompts, start=1):
        if not isinstance(shot, dict):
            raise ValueError(f"Shot {index} must be an object")

        missing = REQUIRED_STORYBOARD_FIELDS - set(shot)
        if missing:
            raise ValueError(f"Shot {index} missing fields: {', '.join(sorted(missing))}")

        shot_number = normalize_shot_number(shot["shot_number"], index)
        shot["shot_number"] = shot_number
        if shot_number != index:
            raise ValueError(f"Shot {index} field shot_number must equal {index}")

        for field in sorted(REQUIRED_STORYBOARD_TEXT_FIELDS):
            value = shot[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Shot {index} field {field} must be a non-empty string")

        beat_id = shot.get("beat_id")
        if require_beat_ids and (not isinstance(beat_id, str) or not beat_id.strip()):
            raise ValueError(f"Shot {index} field beat_id must be a non-empty string")
        if isinstance(beat_id, str):
            shot["beat_id"] = beat_id.strip()
            observed_beat_ids.add(shot["beat_id"])
            if allowed_beat_ids and shot["beat_id"] not in allowed_beat_ids:
                raise ValueError(
                    f"Shot {index} field beat_id must reference a body pacing beat"
                )

        character_names = shot.get("character_names", [])
        if not isinstance(character_names, list):
            raise ValueError(f"Shot {index} field character_names must be a list")
        shot["character_names"] = [
            name.strip() for name in character_names if isinstance(name, str) and name.strip()
        ]
        has_explicit_duration = "duration_seconds" in shot
        shot["duration_seconds"] = normalize_duration_seconds(
            shot.get("duration_seconds", shot["duration"]),
            index,
            strict=has_explicit_duration,
        )
        total_duration += shot["duration_seconds"]

    missing_beat_ids = set(required_beat_ids or []) - observed_beat_ids
    if missing_beat_ids:
        raise ValueError(
            "Storyboard must cover every body pacing beat_id; missing: "
            + ", ".join(sorted(missing_beat_ids))
        )

    if expected_duration_seconds is None and not (
        EPISODE_DURATION_MIN_SECONDS <= total_duration <= EPISODE_DURATION_MAX_SECONDS
    ):
        raise ValueError(
            f"分镜总时长 duration 为 {total_duration} 秒，必须在 "
            f"{EPISODE_DURATION_MIN_SECONDS} 到 {EPISODE_DURATION_MAX_SECONDS} 秒之间"
        )
    if expected_duration_seconds is not None and total_duration != expected_duration_seconds:
        raise ValueError(
            f"Expected storyboard duration {expected_duration_seconds} seconds, got {total_duration}"
        )
    return storyboard_prompts


def resolve_cold_open_payload(payload, storyboard_prompts, pacing_cold_open):
    if not pacing_cold_open:
        return {}

    generated = payload.get("cold_open")
    if not isinstance(generated, dict):
        raise ValueError("Model response must include a cold_open object")

    source_beat_id = pacing_cold_open["source_beat_id"]
    if generated.get("source_beat_id") != source_beat_id:
        raise ValueError("cold_open source_beat_id must match pacing cold_open")
    matching_shots = [
        shot for shot in storyboard_prompts if shot.get("beat_id") == source_beat_id
    ]
    if not matching_shots:
        raise ValueError(
            "cold_open source_beat_id must match at least one storyboard shot beat_id"
        )

    requested_number = _optional_int(generated.get("source_shot_number"))
    source_shot = next(
        (
            shot
            for shot in matching_shots
            if shot["shot_number"] == requested_number
        ),
        matching_shots[-1],
    )
    duration_ms = pacing_cold_open["duration_seconds"] * 1000
    shot_duration_ms = source_shot["duration_seconds"] * 1000
    trim_start_ms = _optional_int(
        generated.get("trim_start_ms", generated.get("in_ms"))
    )
    if trim_start_ms is None:
        trim_start_ms = 0
    trim_start_ms = max(0, min(trim_start_ms, shot_duration_ms - duration_ms))
    trim_end_ms = trim_start_ms + duration_ms

    return {
        **pacing_cold_open,
        "source_shot_number": source_shot["shot_number"],
        "trim_start_ms": trim_start_ms,
        "trim_end_ms": trim_end_ms,
    }


def _pacing_cold_open(pacing):
    if not isinstance(pacing, dict):
        return {}
    if pacing.get("profile_version") == LEGACY_PACING_PROFILE_VERSION:
        return {}
    candidate = pacing.get("cold_open")
    if not isinstance(candidate, dict):
        return {}
    source_beat_id = candidate.get("source_beat_id")
    if not isinstance(source_beat_id, str) or not source_beat_id.strip():
        return {}
    duration = candidate.get("duration_seconds")
    if duration != COLD_OPEN_DURATION_SECONDS:
        raise ValueError(
            f"cold_open duration_seconds must equal {COLD_OPEN_DURATION_SECONDS}"
        )
    return {
        "hook_type": str(candidate.get("hook_type") or "reversal_dialogue"),
        "source_beat_id": source_beat_id.strip(),
        "duration_seconds": duration,
        "withheld_reveal": str(candidate.get("withheld_reveal") or ""),
        "return_bridge": str(candidate.get("return_bridge") or "两小时前"),
    }


def _body_beat_ids(pacing):
    beats = pacing.get("beats") if isinstance(pacing, dict) else None
    if not isinstance(beats, list):
        raise ValueError("short_drama_v3 pacing must include beats")
    beat_ids = []
    for beat in beats:
        if not isinstance(beat, dict) or beat.get("beat_type") == "crisis_open":
            continue
        beat_id = beat.get("beat_id")
        if not isinstance(beat_id, str) or not beat_id.strip():
            raise ValueError("short_drama_v3 body pacing beats must include beat_id")
        beat_ids.append(beat_id.strip())
    if not beat_ids:
        raise ValueError("short_drama_v3 pacing must include body beats")
    return beat_ids


def _optional_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def normalize_shot_number(value, index):
    if isinstance(value, bool):
        raise ValueError(f"第 {index} 个镜头的 shot_number 必须是整数")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
        match = re.search(r"\d+", stripped)
        if match:
            return int(match.group(0))
    raise ValueError(f"第 {index} 个镜头的 shot_number 必须是整数")


def normalize_duration_seconds(value, index, strict=True):
    if isinstance(value, bool):
        raise ValueError(f"第 {index} 个镜头的 duration_seconds 必须是整数")
    if isinstance(value, int):
        seconds = value
    else:
        match = re.search(r"\d+", str(value or ""))
        seconds = int(match.group(0)) if match else 5
    if strict and not MIN_SHOT_DURATION_SECONDS <= seconds <= MAX_SHOT_DURATION_SECONDS:
        raise ValueError(
            f"第 {index} 个镜头的 duration_seconds 必须在 "
            f"{MIN_SHOT_DURATION_SECONDS} 到 {MAX_SHOT_DURATION_SECONDS} 之间"
        )
    return max(MIN_SHOT_DURATION_SECONDS, min(MAX_SHOT_DURATION_SECONDS, seconds))


def _pacing_duration(pacing):
    if not isinstance(pacing, dict):
        return EPISODE_DURATION_TARGET_SECONDS
    duration = pacing.get("duration_seconds")
    maximum_duration = (
        LEGACY_EPISODE_DURATION_MAX_SECONDS
        if pacing.get("profile_version") == LEGACY_PACING_PROFILE_VERSION
        else EPISODE_DURATION_MAX_SECONDS
    )
    if (
        isinstance(duration, int)
        and not isinstance(duration, bool)
        and EPISODE_DURATION_MIN_SECONDS <= duration <= maximum_duration
    ):
        return duration
    return EPISODE_DURATION_TARGET_SECONDS
