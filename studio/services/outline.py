import json

from studio.constants import (
    EPISODE_COUNT,
    EPISODE_DURATION_MAX_SECONDS,
    EPISODE_DURATION_MIN_SECONDS,
    EPISODE_DURATION_TARGET_SECONDS,
    MAX_INFORMATION_GAP_SECONDS,
    NEXT_CRISIS_MAX_SECONDS,
    NEXT_CRISIS_MIN_SECONDS,
    OUTLINE_CANDIDATE_COUNT,
)


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
    messages = [
        {
            "role": "system",
            "content": (
                "你是爆款 AI 漫剧策划。只返回 JSON，不要返回 Markdown。"
                'JSON 格式为 {"outlines": [...]}。'
            ),
        },
        {
            "role": "user",
            "content": (
                f"题材：{genre}\n"
                f"目标：生成 {OUTLINE_CANDIDATE_COUNT} 个 AI 漫剧大纲候选。\n"
                f"固定规格：{EPISODE_COUNT}集；单集必须控制在 "
                f"{EPISODE_DURATION_MIN_SECONDS} 到 {EPISODE_DURATION_MAX_SECONDS} 秒，"
                f"默认按 {EPISODE_DURATION_TARGET_SECONDS} 秒设计。内容超量时删除重复说明、"
                "合并同功能事件，不得扩展时间轴或压缩语速。\n"
                f"每个候选必须包含 {REQUIRED_OUTLINE_FIELD_LIST}。"
                "核心设定由你随机生成，要适合高密度短视频漫剧；"
                "每集只安排一个目标、一条主冲突链和一次核心反转，主要角色不超过3人、"
                "场景不超过2个。前 3 秒必须截取本集后段真实剧情作为片花，不能另写"
                "不会在正片兑现的开场；随后明确目标、升级两次阻碍并兑现反转。"
                f"有效信息空档不得超过 {MAX_INFORMATION_GAP_SECONDS} 秒，结尾仅用 "
                f"{NEXT_CRISIS_MIN_SECONDS} 到 {NEXT_CRISIS_MAX_SECONDS} 秒开启下集新问题。"
            ),
        },
    ]
    messages[-1]["content"] += (
        "\nOutput contract: Return exactly six outline objects. "
        "All field values must be non-empty strings; never use null, arrays, or objects. "
        "Every id must be short and unique. Field meanings: "
        "title is the series title; core_premise is the central premise; "
        "protagonist describes the lead character; hook states the audience hook; "
        "arc_summary is a detailed long-running story arc covering the opening setup, "
        "escalating stages, climax, and ending direction. "
        "Use this exact object shape for every item: "
        '{"id":"...","title":"...","core_premise":"...",'
        '"protagonist":"...","hook":"...","arc_summary":"..."}.'
    )
    payload = provider.generate_json(messages, temperature=0.9)
    try:
        return validate_outlines(payload)
    except ValueError as exc:
        correction_messages = messages + [
            {
                "role": "assistant",
                "content": json.dumps(payload, ensure_ascii=False),
            },
            {
                "role": "user",
                "content": (
                    f"Previous JSON failed validation: {exc}. "
                    "Correct the entire response while preserving six distinct candidates. "
                    "All field values must be non-empty strings; never use null. "
                    "Return only the complete corrected JSON object."
                ),
            },
        ]
        corrected_payload = provider.generate_json(correction_messages, temperature=0.3)
        return validate_outlines(corrected_payload)


def validate_outlines(payload):
    outlines = payload.get("outlines") if isinstance(payload, dict) else None
    if not isinstance(outlines, list):
        raise ValueError("Model response must include an outlines list")
    if len(outlines) != OUTLINE_CANDIDATE_COUNT:
        raise ValueError(f"Expected {OUTLINE_CANDIDATE_COUNT} outlines, got {len(outlines)}")

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
