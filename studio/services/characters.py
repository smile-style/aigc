REQUIRED_CHARACTER_FIELDS = {
    "name",
    "role",
    "appearance",
    "personality",
    "costume",
    "image_prompt",
}


def generate_character_profiles(provider, outline, episodes):
    if not isinstance(outline, dict):
        raise ValueError("Selected outline is required")
    episode_context = "\n".join(
        f"第 {episode.get('episode')} 集：{episode.get('title')}；{episode.get('summary')}"
        for episode in episodes[:12]
    )
    payload = provider.generate_json(
        [
            {
                "role": "system",
                "content": (
                    "你是影视角色概念设计师。只返回 JSON，不要返回 Markdown。"
                    '格式为 {"characters": [...]}。'
                ),
            },
            {
                "role": "user",
                "content": (
                    "根据下面的作品设定提取 2 到 8 个核心人物，并生成可用于角色原图的提示词。\n"
                    f"标题：{outline.get('title', '')}\n"
                    f"核心设定：{outline.get('core_premise', '')}\n"
                    f"主角：{outline.get('protagonist', '')}\n"
                    f"长线梗概：{outline.get('arc_summary', '')}\n"
                    f"剧集信息：\n{episode_context}\n"
                    "每个角色必须包含 name、role、appearance、personality、costume、image_prompt。"
                    "image_prompt 要求单人全身角色设定图、正面三分之二视角、纯色背景、"
                    "固定五官发型服装配色、无文字无水印，适合作为后续分镜的一致性参考图。"
                ),
            },
        ],
        temperature=0.6,
    )
    return validate_character_profiles(payload)


def validate_character_profiles(payload):
    if not isinstance(payload, dict):
        raise ValueError("Model response must be an object")
    characters = payload.get("characters")
    if not isinstance(characters, list) or not 1 <= len(characters) <= 12:
        raise ValueError("Model response must include 1 to 12 characters")
    validated = []
    for index, character in enumerate(characters, start=1):
        if not isinstance(character, dict):
            raise ValueError(f"Character {index} must be an object")
        missing = REQUIRED_CHARACTER_FIELDS - set(character)
        if missing:
            raise ValueError(f"Character {index} missing fields: {', '.join(sorted(missing))}")
        item = {}
        for field in REQUIRED_CHARACTER_FIELDS:
            value = character[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Character {index} field {field} must be non-empty")
            item[field] = value.strip()
        validated.append(item)
    return validated
