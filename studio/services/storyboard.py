import re

from studio.constants import MAX_STORYBOARD_SHOTS, MIN_STORYBOARD_SHOTS


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


def generate_storyboard(provider, episode_script, episode_number=1):
    if not isinstance(episode_script, str) or not episode_script.strip():
        raise ValueError("episode_script must be a non-empty string")

    payload = provider.generate_json(
        [
            {
                "role": "system",
                "content": (
                    "你是 AI 短剧分镜设计师。只返回 JSON，不要返回 Markdown 或额外说明。"
                    'JSON 格式为 {"storyboard_prompts": [...]}。'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"请将下面这个 2 分钟第 {episode_number} 集剧本拆解成适合 AI 漫画和视频生成的分镜。\n"
                    f"分镜数量必须在 {MIN_STORYBOARD_SHOTS} 到 {MAX_STORYBOARD_SHOTS} 之间。\n"
                    "storyboard_prompts 中的每个分镜都必须包含字段："
                    f"{REQUIRED_STORYBOARD_FIELD_LIST}。\n"
                    "shot_number 尽量使用整数；如果输出成字符串，也必须能明确解析为顺序编号。\n"
                    "所有文本字段都必须是非空字符串，内容要具体、可直接用于图像和视频生成。\n"
                    "另外输出 character_names 字符串数组和 duration_seconds 整数；"
                    "character_names 只填写本镜头实际出场的角色姓名，duration_seconds 范围为 2 到 15。\n"
                    f"第 {episode_number} 集剧本如下：\n{episode_script}"
                ),
            },
        ],
        temperature=0.6,
    )
    return validate_storyboard_payload(payload)


def validate_storyboard_payload(payload):
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
    return storyboard_prompts


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
    if strict and not 2 <= seconds <= 15:
        raise ValueError(f"第 {index} 个镜头的 duration_seconds 必须在 2 到 15 之间")
    return max(2, min(15, seconds))
