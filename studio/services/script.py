from studio.constants import EPISODE_COUNT, EPISODE_DURATION_MINUTES


REQUIRED_EPISODE_FIELDS = {"episode", "title", "summary", "key_conflict", "cliffhanger"}
REQUIRED_EPISODE_FIELD_LIST = ", ".join(
    ["episode", "title", "summary", "key_conflict", "cliffhanger"]
)


def generate_script(provider, outline):
    payload = provider.generate_json(
        [
            {
                "role": "system",
                "content": (
                    "你是爆款AI漫画短剧编剧。只返回JSON，不要返回markdown。"
                    'JSON格式为 {"script_plan": [...], "episode_1_script": "..."}。'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"请基于以下大纲生成完整分集规划和第1集完整剧本样稿。\n"
                    f"标题：{outline['title']}\n"
                    f"核心设定：{outline['core_premise']}\n"
                    f"主角：{outline['protagonist']}\n"
                    f"钩子：{outline['hook']}\n"
                    f"主线梗概：{outline['arc_summary']}\n"
                    f"固定规格：共{EPISODE_COUNT}集，每集{EPISODE_DURATION_MINUTES}分钟。\n"
                    f"script_plan 必须包含 {EPISODE_COUNT} 条，每条都必须包含字段："
                    f"{REQUIRED_EPISODE_FIELD_LIST}。\n"
                    "episode 字段必须按 1 到 60 递增。"
                    "同时输出第1集完整剧本样稿 episode_1_script，内容要完整、可直接用于创作。"
                ),
            },
        ],
        temperature=0.7,
    )
    return validate_script_payload(payload)


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

        if episode["episode"] != index:
            raise ValueError(f"Episode {index} field episode must equal {index}")

        for field in sorted(REQUIRED_EPISODE_FIELDS - {"episode"}):
            value = episode[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Episode {index} field {field} must be a non-empty string")

    return {"script_plan": script_plan, "episode_1_script": episode_1_script}
