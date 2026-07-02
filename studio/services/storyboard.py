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


def generate_storyboard(provider, episode_1_script):
    if not isinstance(episode_1_script, str) or not episode_1_script.strip():
        raise ValueError("episode_1_script must be a non-empty string")

    payload = provider.generate_json(
        [
            {
                "role": "system",
                "content": (
                    "你是AI短剧分镜设计师。只返回JSON，不要返回markdown或额外说明。"
                    'JSON格式为 {"storyboard_prompts": [...]}。'
                ),
            },
            {
                "role": "user",
                "content": (
                    "请将下面这个2分钟第1集剧本拆解成适合AI漫画和视频生成的分镜。"
                    f"分镜数量必须在{MIN_STORYBOARD_SHOTS}到{MAX_STORYBOARD_SHOTS}之间。\n"
                    "storyboard_prompts 中的每个分镜都必须包含字段："
                    f"{REQUIRED_STORYBOARD_FIELD_LIST}。\n"
                    f"shot_number 必须从1到分镜总数按顺序递增，且总数必须在{MIN_STORYBOARD_SHOTS}到"
                    f"{MAX_STORYBOARD_SHOTS}之间。\n"
                    "所有文本字段都必须是非空字符串，内容要具体、可直接用于图像和视频生成。\n"
                    f"第1集剧本如下：\n{episode_1_script}"
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

        shot_number = shot["shot_number"]
        if not isinstance(shot_number, int) or isinstance(shot_number, bool):
            raise ValueError(f"Shot {index} field shot_number must be an integer number")
        if shot_number != index:
            raise ValueError(f"Shot {index} field shot_number must equal {index}")

        for field in sorted(REQUIRED_STORYBOARD_TEXT_FIELDS):
            value = shot[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Shot {index} field {field} must be a non-empty string")

    return storyboard_prompts
