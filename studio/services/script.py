from studio.constants import EPISODE_COUNT, EPISODE_DURATION_MINUTES


REQUIRED_EPISODE_FIELDS = {"episode", "title", "summary", "key_conflict", "cliffhanger"}
REQUIRED_EPISODE_FIELD_LIST = ", ".join(sorted(REQUIRED_EPISODE_FIELDS))
REQUIRED_OUTLINE_FIELDS = {
    "title",
    "core_premise",
    "protagonist",
    "hook",
    "arc_summary",
}


def generate_script(provider, outline):
    validated_outline = validate_outline(outline)
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
                    "请基于以下大纲生成完整分集规划和第1集完整剧本样稿。\n"
                    f"标题：{validated_outline['title']}\n"
                    f"核心设定：{validated_outline['core_premise']}\n"
                    f"主角：{validated_outline['protagonist']}\n"
                    f"钩子：{validated_outline['hook']}\n"
                    f"主线梗概：{validated_outline['arc_summary']}\n"
                    f"固定规格：共{EPISODE_COUNT}集，每集{EPISODE_DURATION_MINUTES}分钟。\n"
                    f"script_plan 必须包含 {EPISODE_COUNT} 条，每条都必须包含字段："
                    f"{REQUIRED_EPISODE_FIELD_LIST}。\n"
                    f"episode 字段必须按 1 到 {EPISODE_COUNT} 递增，且必须是整数。\n"
                    "同时输出第1集完整剧本样稿 episode_1_script，内容要完整、可直接用于创作。"
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

    return {"script_plan": script_plan, "episode_1_script": episode_1_script}

def generate_episode_script(provider, outline, episode, previous_episode=None, next_episode=None):
    validated_outline = validate_outline(outline)
    if not isinstance(episode, dict):
        raise ValueError("Episode must be an object")

    required_fields = REQUIRED_EPISODE_FIELDS
    missing = required_fields - set(episode)
    if missing:
        raise ValueError(f"Episode missing fields: {', '.join(sorted(missing))}")
    episode_number = episode["episode"]
    if not isinstance(episode_number, int) or isinstance(episode_number, bool):
        raise ValueError("Episode number must be an integer")

    for field in sorted(required_fields - {"episode"}):
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

    payload = provider.generate_json(
        [
            {
                "role": "system",
                "content": (
                    "你是爆款 AI 漫画短剧编剧。只返回 JSON，不要返回 Markdown。"
                    'JSON 格式为 {"episode_script": "..."}。'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"请创作第 {episode_number} 集完整剧本，时长约 "
                    f"{EPISODE_DURATION_MINUTES} 分钟，可直接用于分镜制作。\n"
                    f"整部作品标题：{validated_outline['title']}\n"
                    f"核心设定：{validated_outline['core_premise']}\n"
                    f"主角：{validated_outline['protagonist']}\n"
                    f"长线梗概：{validated_outline['arc_summary']}\n"
                    f"本集标题：{episode['title']}\n"
                    f"本集摘要：{episode['summary']}\n"
                    f"核心冲突：{episode['key_conflict']}\n"
                    f"结尾悬念：{episode['cliffhanger']}\n"
                    + ("\n".join(continuity) if continuity else "")
                    + "\n剧本必须包含场景、人物动作、对白和旁白，并与前后集自然衔接。"
                ),
            },
        ],
        temperature=0.7,
    )
    if not isinstance(payload, dict):
        raise ValueError("Model response must be an object")
    episode_script = payload.get("episode_script")
    if not isinstance(episode_script, str) or not episode_script.strip():
        raise ValueError("Model response must include a non-empty episode_script")
    return episode_script
