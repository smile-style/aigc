REQUIRED_CHARACTER_FIELDS = {
    "name",
    "role",
    "appearance",
    "personality",
    "costume",
    "image_prompt",
}

CHARACTER_VISUAL_STYLES = {
    "comic": {
        "label": "漫画",
        "profile_instruction": "统一使用精致国漫插画风，线稿干净，赛璐璐上色，人物五官有表现力且保持一致，禁止真人摄影和3D渲染。",
        "image_lock": (
            "Visual style lock: polished Chinese comic illustration, clean line art, cel shading, "
            "expressive but consistent facial design. Do not use photorealism, live-action photography, "
            "3D rendering or chibi styling."
        ),
    },
    "realistic": {
        "label": "偏真人",
        "profile_instruction": "统一使用电影级偏真人画风，真实皮肤质感与自然人体结构，真人影视服化道和灯光，禁止动漫、插画、3D和Q版风格。",
        "image_lock": (
            "Visual style lock: cinematic photorealism, realistic skin texture, natural facial anatomy, "
            "live-action wardrobe and lighting. Do not use anime, manga, illustration, 3D rendering "
            "or chibi styling."
        ),
    },
}


def normalize_character_visual_style(visual_style):
    style = str(visual_style or "comic").strip().lower()
    if style not in CHARACTER_VISUAL_STYLES:
        raise ValueError(f"Unsupported character visual style: {style}")
    return style


def apply_character_visual_style(prompt, visual_style):
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Character image prompt must be a non-empty string")
    style = normalize_character_visual_style(visual_style)
    style_lock = CHARACTER_VISUAL_STYLES[style]["image_lock"]
    if style_lock in prompt:
        return prompt.strip()
    return f"{prompt.strip()}\n{style_lock}"


def generate_character_profiles(
    provider,
    outline,
    episodes,
    visual_style="comic",
    episode_focus=None,
    existing_character_names=None,
):
    if not isinstance(outline, dict):
        raise ValueError("Selected outline is required")
    style = normalize_character_visual_style(visual_style)
    style_config = CHARACTER_VISUAL_STYLES[style]
    episode_context = "\n".join(
        f"第 {episode.get('episode')} 集：{episode.get('title')}；{episode.get('summary')}"
        for episode in episodes[:12]
    )
    if episode_focus:
        existing_names = ", ".join(existing_character_names or []) or "(none)"
        episode_context = (
            f"Current episode {episode_focus.get('episode', '')}: "
            f"{episode_focus.get('title', '')}\n"
            f"Summary: {episode_focus.get('summary', '')}\n"
            f"Full script:\n{episode_focus.get('full_script', '')}\n"
            f"Existing character names: {existing_names}\n"
            "Return only new named characters introduced in the current episode. "
            "Do not return existing characters, unnamed extras, crowds, or generic roles. "
            "If there are no new named characters, return {\"characters\": []}."
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
                    f"全体角色统一画风：{style_config['label']}。{style_config['profile_instruction']}\n"
                    "每个角色必须包含 name、role、appearance、personality、costume、image_prompt。"
                    "image_prompt 要求单人全身角色设定图、正面三分之二视角、纯色背景，"
                    "固定五官发型服装配色、无文字无水印，适合作为后续分镜的一致性参考图。"
                ),
            },
        ],
        temperature=0.6,
    )
    profiles = validate_character_profiles(payload, allow_empty=bool(episode_focus))
    for profile in profiles:
        profile["image_prompt"] = apply_character_visual_style(
            profile["image_prompt"],
            style,
        )
    return profiles


def validate_character_profiles(payload, allow_empty=False):
    if not isinstance(payload, dict):
        raise ValueError("Model response must be an object")
    characters = payload.get("characters")
    minimum = 0 if allow_empty else 1
    if not isinstance(characters, list) or not minimum <= len(characters) <= 12:
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


def normalize_character_name(value):
    return " ".join(str(value or "").strip().split())


def storyboard_character_candidates(script):
    from studio.models import StoryboardShot

    existing = {
        normalize_character_name(name)
        for name in script.characters.filter(is_deleted=False).values_list("name", flat=True)
    }
    candidates = {}
    shots = StoryboardShot.objects.filter(storyboard__script=script).select_related(
        "storyboard__episode"
    )
    for shot in shots:
        for raw_name in shot.character_names or []:
            name = normalize_character_name(raw_name)
            if not name or name in existing:
                continue
            item = candidates.setdefault(
                name,
                {"name": name, "shot_count": 0, "episodes": set(), "contexts": []},
            )
            item["shot_count"] += 1
            item["episodes"].add(shot.storyboard.episode.episode_number)
            context = " ".join(
                part.strip()
                for part in (shot.visual_description, shot.character_action, shot.image_prompt)
                if part and part.strip()
            )
            if context and context not in item["contexts"]:
                item["contexts"].append(context)
    return [
        {
            "name": item["name"],
            "shot_count": item["shot_count"],
            "episode_numbers": sorted(item["episodes"]),
            "context": " ".join(item["contexts"][:4])[:1600],
        }
        for item in candidates.values()
    ]


def create_storyboard_characters(script, selected_names):
    from django.db import transaction
    from django.db.models import Max

    from studio.models import Character
    from studio.repositories.workspace import WorkspaceRepository

    candidates = {item["name"]: item for item in storyboard_character_candidates(script)}
    names = []
    for value in selected_names:
        name = normalize_character_name(value)
        if name and name not in names:
            names.append(name)
    invalid = [name for name in names if name not in candidates]
    if invalid:
        raise ValueError("Selected characters already exist or are no longer in the storyboard.")
    if not names:
        raise ValueError("Select at least one character to generate.")

    tasks = []
    repository = WorkspaceRepository()
    with transaction.atomic():
        next_position = script.characters.aggregate(value=Max("position"))["value"] or 0
        for name in names:
            next_position += 1
            candidate = candidates[name]
            context = candidate["context"] or f"{name} appears in the storyboard"
            image_prompt = (
                f"Character name: {name}. Keep the design consistent with these storyboard scenes: "
                f"{context}. Single character full-body reference sheet, front three-quarter view, "
                "consistent face, hairstyle, costume and color palette, plain background, "
                "no text, no watermark."
            )
            character, _ = Character.objects.update_or_create(
                script=script,
                name=name,
                defaults={
                    "role": "Storyboard character",
                    "appearance": context,
                    "personality": "Keep behavior consistent with the script",
                    "costume": "Keep wardrobe consistent across the full script",
                    "image_prompt": image_prompt,
                    "position": next_position,
                    "is_deleted": False,
                    "deleted_at": None,
                },
            )
            tasks.append(
                repository.create_character_image_task(
                    script.project.workspace_id,
                    character.id,
                    image_prompt,
                )
            )
    return tasks
