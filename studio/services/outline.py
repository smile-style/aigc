from studio.constants import EPISODE_COUNT, EPISODE_DURATION_MINUTES, OUTLINE_CANDIDATE_COUNT


REQUIRED_OUTLINE_FIELDS = {
    "id",
    "title",
    "core_premise",
    "protagonist",
    "hook",
    "arc_summary",
}
REQUIRED_OUTLINE_FIELD_LIST = ", ".join(sorted(REQUIRED_OUTLINE_FIELDS))


def generate_outlines(provider, genre):
    payload = provider.generate_json(
        [
            {
                "role": "system",
                "content": (
                    "你是爆款AI漫剧策划。只返回JSON，不要返回Markdown。"
                    'JSON格式为 {"outlines": [...]}。'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"题材：{genre}\n"
                    f"目标：生成{OUTLINE_CANDIDATE_COUNT}个AI漫剧大纲候选。\n"
                    f"固定规格：{EPISODE_COUNT}集，每集{EPISODE_DURATION_MINUTES}分钟。\n"
                    f"每个候选必须包含 {REQUIRED_OUTLINE_FIELD_LIST}。"
                    "核心设定由你随机生成，要适合短视频漫剧。"
                ),
            },
        ],
        temperature=0.9,
    )
    return validate_outlines(payload)


def validate_outlines(payload):
    outlines = payload.get("outlines") if isinstance(payload, dict) else None
    if not isinstance(outlines, list):
        raise ValueError("Model response must include an outlines list")
    if len(outlines) != OUTLINE_CANDIDATE_COUNT:
        raise ValueError(f"Expected {OUTLINE_CANDIDATE_COUNT} outlines")

    seen_ids = set()
    for index, outline in enumerate(outlines, start=1):
        if not isinstance(outline, dict):
            raise ValueError(f"Outline {index} must be an object")
        missing = REQUIRED_OUTLINE_FIELDS - set(outline)
        if missing:
            raise ValueError(f"Outline {index} missing fields: {', '.join(sorted(missing))}")
        for field in sorted(REQUIRED_OUTLINE_FIELDS):
            value = outline[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Outline {index} field {field} must be a non-empty string")

        outline_id = outline["id"]
        if outline_id in seen_ids:
            raise ValueError(f"Outline {index} has duplicate id: {outline_id}")
        seen_ids.add(outline_id)
    return outlines
