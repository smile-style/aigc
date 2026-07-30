import hashlib
from io import BytesIO

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max
from PIL import Image, ImageDraw, ImageOps

from studio.models import CoverTemplate, CoverTemplateVersion, EpisodeCover, GenerationTask, Script

from .cover_common import (
    default_episode_cover_title,
    load_font as _load_font,
    normalize_cover_title,
    split_cover_title,
    text_size as _text_size,
)


LANDSCAPE_SIZE = (1600, 1200)
PORTRAIT_SIZE = (1200, 1600)
XIAOHONGSHU_SIZE = (1050, 1500)
DOUYIN_SIZE = (1080, 1620)
LANDSCAPE_GENERATION_SIZE = "1536x1024"
PORTRAIT_GENERATION_SIZE = "1024x1536"
XIAOHONGSHU_GENERATION_SIZE = "1024x1536"
DOUYIN_GENERATION_SIZE = "1024x1536"


def build_cover_prompt(script):
    outline = script.outline
    protagonist = (
        script.characters.filter(is_deleted=False)
        .order_by("position", "id")
        .first()
    )
    character_details = ""
    if protagonist:
        character_details = (
            f"主角姓名：{protagonist.name}；外观：{protagonist.appearance}；"
            f"服装：{protagonist.costume}。"
        )
    visual_style = "精致国漫插画、干净线稿、电影级光影"
    if script.character_visual_style == Script.CHARACTER_STYLE_REALISTIC:
        visual_style = "电影级写实质感、真实皮肤与自然人体结构"
    return (
        "为短视频漫剧设计一套横竖版系列封面母版。"
        f"作品剧名《{outline.title}》，核心设定：{outline.core_premise}。"
        f"主角设定：{outline.protagonist}。{character_details}"
        f"视觉风格：{visual_style}。"
        "画面必须以同一位主角为明确视觉中心，面部清晰，身份特征和服装稳定；"
        "通过关键危机道具和环境体现冲突，并为剧名、集数和单集标题预留高对比标题区域。"
        "适合手机信息流缩略图，主体轮廓明确，色彩层次清晰。"
        "只生成无字底图：无文字、无字母、无数字、无标志、无水印、无边框。"
    )


def cover_prompt_for_variant(prompt, variant):
    prompt = str(prompt or "").strip()
    if variant == "portrait":
        layout = (
            "生成3:4竖封面构图。主角占画面中上部，完整保留头部和上半身；"
            "顶部预留剧名区域，底部预留单集标题区域，重要内容保持在中央安全区。"
        )
    elif variant == "landscape":
        layout = (
            "生成4:3横封面构图。主角位于画面右侧或中央偏右；"
            "左上预留剧名区域，底部预留单集标题区域，重要内容保持在中央安全区。"
        )
    elif variant == "xiaohongshu":
        layout = (
            "生成7:10小红书封面构图。主角位于画面中央偏上，完整保留头部和上半身，"
            "底部预留剧名和主角名区域，重要内容保持在中央安全区。"
        )
    elif variant == "douyin":
        layout = (
            "生成2:3抖音封面构图。主角位于画面中央偏上，面部清晰且视觉冲击强，"
            "底部预留剧名和主角名区域，重要内容保持在中央安全区。"
        )
    else:
        raise ValueError(f"Unsupported cover variant: {variant}")
    return f"{prompt}\n{layout}画面中只出现一位主角。只生成无字底图。"


@transaction.atomic
def save_cover_template(
    script,
    landscape_result,
    portrait_result=None,
    prompt_snapshot="",
    *,
    xiaohongshu_result=None,
    douyin_result=None,
):
    if isinstance(portrait_result, str) and not prompt_snapshot:
        prompt_snapshot = portrait_result
        portrait_result = None
    portrait_result = portrait_result or landscape_result
    template, _ = CoverTemplate.objects.select_for_update().get_or_create(
        script=script,
        defaults={
            "prompt_snapshot": prompt_snapshot,
            "model": landscape_result.model,
            "source_url": landscape_result.source_url,
            "version": 1,
        },
    )
    latest_version = template.versions.aggregate(value=Max("version"))["value"] or 0
    next_version = latest_version + 1
    style_payload = {
        "show_title": script.outline.title,
        "formats": {
            "landscape": {"canvas": list(LANDSCAPE_SIZE), "aspect_ratio": "4:3"},
            "portrait": {"canvas": list(PORTRAIT_SIZE), "aspect_ratio": "3:4"},
            "xiaohongshu": {"canvas": list(XIAOHONGSHU_SIZE), "aspect_ratio": "7:10"},
            "douyin": {"canvas": list(DOUYIN_SIZE), "aspect_ratio": "2:3"},
        },
        "title_color": "#FFFFFF",
        "accent_color": "#FFCA3A",
        "portrait_model": portrait_result.model,
        "portrait_source_url": portrait_result.source_url,
        "xiaohongshu_model": getattr(xiaohongshu_result, "model", ""),
        "douyin_model": getattr(douyin_result, "model", ""),
    }
    version = CoverTemplateVersion(
        template=template,
        prompt_snapshot=prompt_snapshot,
        source_url=landscape_result.source_url,
        model=landscape_result.model,
        style_payload=style_payload,
        version=next_version,
    )
    version.background.save(
        f"master-landscape-v{next_version:03d}.png",
        ContentFile(_fit_master(landscape_result.content, LANDSCAPE_SIZE)),
        save=False,
    )
    version.portrait_background.save(
        f"master-portrait-v{next_version:03d}.png",
        ContentFile(_fit_master(portrait_result.content, PORTRAIT_SIZE)),
        save=False,
    )
    protagonist = (
        script.characters.filter(is_deleted=False)
        .order_by("position", "id")
        .first()
    )
    protagonist_name = protagonist.name if protagonist else script.outline.protagonist
    if xiaohongshu_result is not None:
        version.xiaohongshu_background.save(
            f"master-xiaohongshu-v{next_version:03d}.png",
            ContentFile(
                _compose_platform_master(
                    xiaohongshu_result.content,
                    XIAOHONGSHU_SIZE,
                    script.outline.title,
                    protagonist_name,
                )
            ),
            save=False,
        )
    if douyin_result is not None:
        version.douyin_background.save(
            f"master-douyin-v{next_version:03d}.png",
            ContentFile(
                _compose_platform_master(
                    douyin_result.content, DOUYIN_SIZE, script.outline.title, protagonist_name
                )
            ),
            save=False,
        )
    version.save()

    template.version = next_version
    template.prompt_snapshot = prompt_snapshot
    template.model = landscape_result.model
    template.source_url = landscape_result.source_url
    template.style_payload = style_payload
    template.background = version.background.name
    template.portrait_background = version.portrait_background.name
    template.xiaohongshu_background = version.xiaohongshu_background.name
    template.douyin_background = version.douyin_background.name
    template.save()
    _render_all_episode_covers(template)
    return template


@transaction.atomic
def switch_cover_template_version(template, version_number):
    template = CoverTemplate.objects.select_for_update().get(pk=template.pk)
    selected = template.versions.get(version=version_number)
    if template.version == selected.version:
        return template

    template.version = selected.version
    template.prompt_snapshot = selected.prompt_snapshot
    template.background = selected.background.name
    template.portrait_background = selected.portrait_background.name
    template.xiaohongshu_background = selected.xiaohongshu_background.name
    template.douyin_background = selected.douyin_background.name
    template.source_url = selected.source_url
    template.model = selected.model
    template.style_payload = selected.style_payload
    template.save()
    _render_all_episode_covers(template)
    return template


def _render_all_episode_covers(template):
    for episode in template.script.episodes.order_by("episode_number"):
        existing = getattr(episode, "cover", None)
        customized = bool(existing and existing.title_customized)
        title = existing.title if customized else default_episode_cover_title(episode)
        render_episode_cover(template, episode, title, title_customized=customized)


@transaction.atomic
def render_episode_cover(template, episode, title, *, title_customized=False):
    normalized_title = normalize_cover_title(title)
    if episode.script_id != template.script_id:
        raise ValueError("单集和封面母版不属于同一部作品。")

    landscape = _compose_cover(
        template.background,
        LANDSCAPE_SIZE,
        template.script.outline.title,
        normalized_title,
        episode.episode_number,
        portrait=False,
    )
    portrait = None
    if template.portrait_background:
        portrait = _compose_cover(
            template.portrait_background,
            PORTRAIT_SIZE,
            template.script.outline.title,
            normalized_title,
            episode.episode_number,
            portrait=True,
        )

    digest = hashlib.sha1(
        f"{template.version}:{normalized_title}".encode("utf-8")
    ).hexdigest()[:10]
    cover, _ = EpisodeCover.objects.select_for_update().get_or_create(
        episode=episode,
        defaults={
            "template": template,
            "title": normalized_title,
            "title_customized": title_customized,
        },
    )
    old_landscape = cover.image.name if cover.image else ""
    old_portrait = cover.portrait_image.name if cover.portrait_image else ""
    cover.template = template
    cover.title = normalized_title
    cover.title_customized = title_customized
    cover.template_version = template.version
    cover.image.save(
        f"episode-{episode.episode_number:03d}-landscape-v{template.version:03d}-{digest}.jpg",
        ContentFile(landscape),
        save=False,
    )
    if portrait is not None:
        cover.portrait_image.save(
            f"episode-{episode.episode_number:03d}-portrait-v{template.version:03d}-{digest}.jpg",
            ContentFile(portrait),
            save=False,
        )
    else:
        cover.portrait_image = ""
    cover.save()
    _delete_replaced_file(cover.image.storage, old_landscape, cover.image.name)
    _delete_replaced_file(
        cover.portrait_image.storage,
        old_portrait,
        cover.portrait_image.name,
    )
    return cover


def decorate_cover_workspace(workspace):
    script_id = workspace.get("script_id") if isinstance(workspace, dict) else None
    if not script_id:
        workspace["cover_template"] = None
        workspace["cover_template_versions"] = []
        workspace["cover_task"] = None
        workspace["cover_prompt"] = ""
        return workspace

    script = Script.objects.select_related("outline", "project").get(pk=script_id)
    template = getattr(script, "cover_template", None)
    primary_character = (
        script.characters.filter(is_deleted=False)
        .order_by("position", "id")
        .first()
    )
    cover_map = {
        cover.episode_id: cover
        for cover in EpisodeCover.objects.filter(episode__script=script).select_related("episode")
    }
    episode_models = {
        episode.episode_number: episode for episode in script.episodes.all()
    }
    for episode_data in workspace.get("episodes", []):
        episode_model = episode_models.get(episode_data.get("episode"))
        cover = cover_map.get(episode_model.id) if episode_model else None
        automatic_title = default_episode_cover_title(episode_model) if episode_model else ""
        episode_data.update(
            {
                "cover_title": (
                    cover.title if cover and cover.title_customized else automatic_title
                ),
                "cover_image_url": cover.image.url if cover and cover.image else "",
                "cover_portrait_image_url": (
                    cover.portrait_image.url if cover and cover.portrait_image else ""
                ),
                "has_cover": bool(cover and cover.image),
                "has_portrait_cover": bool(cover and cover.portrait_image),
            }
        )
    latest_task = script.project.generation_tasks.filter(
        task_type=GenerationTask.TYPE_COVER_IMAGE,
        target_id=f"script:{script.id}",
    ).order_by("-created_at", "-id").first()
    workspace["cover_show_title"] = script.outline.title
    workspace["cover_protagonist"] = (
        primary_character.name if primary_character else script.outline.protagonist
    )
    workspace["cover_template"] = (
        {
            "id": template.id,
            "background_url": template.background.url,
            "portrait_background_url": (
                template.portrait_background.url if template.portrait_background else ""
            ),
            "xiaohongshu_background_url": (
                template.xiaohongshu_background.url if template.xiaohongshu_background else ""
            ),
            "douyin_background_url": (
                template.douyin_background.url if template.douyin_background else ""
            ),
            "prompt": template.prompt_snapshot,
            "model": template.model,
            "version": template.version,
        }
        if template and template.background
        else None
    )
    workspace["cover_template_versions"] = (
        [
            {
                "id": item.id,
                "background_url": item.background.url,
                "portrait_background_url": (
                    item.portrait_background.url if item.portrait_background else ""
                ),
                "xiaohongshu_background_url": (
                    item.xiaohongshu_background.url if item.xiaohongshu_background else ""
                ),
                "douyin_background_url": (
                    item.douyin_background.url if item.douyin_background else ""
                ),
                "model": item.model,
                "version": item.version,
                "created_at": item.created_at,
                "is_selected": item.version == template.version,
            }
            for item in template.versions.exclude(background="").order_by("-version", "-id")
        ]
        if template
        else []
    )
    workspace["cover_task"] = (
        {
            "id": latest_task.id,
            "status": latest_task.status,
            "error_message": latest_task.error_message,
        }
        if latest_task
        else None
    )
    workspace["cover_prompt"] = (
        template.prompt_snapshot
        if template and template.portrait_background
        else build_cover_prompt(script)
    )
    return workspace


def _fit_master(content, size):
    with Image.open(BytesIO(content)) as source:
        canvas = ImageOps.fit(
            source.convert("RGB"),
            size,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        buffer = BytesIO()
        canvas.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _compose_platform_master(content, size, show_title, protagonist_name):
    with Image.open(BytesIO(content)) as source:
        canvas = ImageOps.fit(
            source.convert("RGB"),
            size,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        ).convert("RGBA")

    width, height = size
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle((0, int(height * 0.62), width, height), fill=(8, 12, 18, 194))
    overlay_draw.rectangle(
        (72, int(height * 0.69), 90, height - 100),
        fill=(255, 202, 58, 255),
    )
    canvas = Image.alpha_composite(canvas, overlay)
    draw = ImageDraw.Draw(canvas)

    title, title_font = _fit_display_text(
        draw, str(show_title or "").strip(), width - 210, 118, 54
    )
    title_y = int(height * 0.72)
    draw.text(
        (126, title_y),
        title,
        font=title_font,
        fill=(255, 255, 255, 255),
        stroke_width=max(3, title_font.size // 24),
        stroke_fill=(8, 12, 18, 235),
    )
    protagonist, protagonist_font = _fit_display_text(
        draw, str(protagonist_name or "").strip(), width - 210, 54, 32
    )
    draw.text(
        (128, title_y + title_font.size + 42),
        protagonist,
        font=protagonist_font,
        fill=(255, 202, 58, 255),
    )

    buffer = BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _compose_cover(field, size, show_title, episode_title, episode_number, *, portrait):
    field.open("rb")
    try:
        with Image.open(field) as source:
            canvas = ImageOps.fit(
                source.convert("RGB"),
                size,
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
    finally:
        field.close()

    width, height = size
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    fade_top = 930 if portrait else 700
    overlay_draw.rectangle((0, fade_top, width, height), fill=(10, 14, 20, 190))
    overlay_draw.rectangle(
        (70 if portrait else 92, fade_top + 55, 88 if portrait else 112, height - 90),
        fill=(255, 202, 58, 255),
    )
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(canvas)

    _draw_show_title(draw, str(show_title or "").strip(), size, portrait=portrait)
    _draw_episode_badge(draw, episode_number, size, portrait=portrait)
    _draw_episode_title(draw, episode_title, size, portrait=portrait)

    buffer = BytesIO()
    canvas.convert("RGB").save(buffer, format="JPEG", quality=92, optimize=True)
    return buffer.getvalue()


def _draw_show_title(draw, value, size, *, portrait):
    if not value:
        return
    width, _ = size
    max_width = 760 if portrait else 1080
    value, font = _fit_display_text(
        draw, value, max_width, 82 if portrait else 88, 36
    )
    x = 72 if portrait else 92
    y = 76 if portrait else 72
    draw.rounded_rectangle(
        (x - 18, y - 14, x + _text_size(draw, value, font)[0] + 18, y + font.size + 18),
        radius=6,
        fill=(8, 12, 18, 186),
    )
    draw.text(
        (x, y),
        value,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=max(2, font.size // 24),
        stroke_fill=(8, 12, 18, 230),
    )


def _draw_episode_badge(draw, episode_number, size, *, portrait):
    width, _ = size
    font = _load_font(42 if portrait else 46)
    badge = f"EP {episode_number:02d}"
    text_width, text_height = _text_size(draw, badge, font)
    right = width - (70 if portrait else 92)
    left = right - text_width - 48
    top = 78 if portrait else 74
    bottom = top + text_height + 28
    draw.rounded_rectangle((left, top, right, bottom), radius=8, fill=(230, 57, 70, 238))
    draw.text((left + 24, top + 10), badge, font=font, fill=(255, 255, 255, 255))


def _draw_episode_title(draw, title, size, *, portrait):
    lines = split_cover_title(title)
    max_width = 920 if portrait else 1280
    max_height = 480 if portrait else 350
    start_size = 164 if portrait else 172
    font = _fit_multiline_font(draw, lines, max_width, max_height, start_size)
    stroke_width = max(5, font.size // 20)
    line_gap = max(12, font.size // 8)
    line_heights = [_text_size(draw, line, font)[1] for line in lines]
    total_height = sum(line_heights) + line_gap * (len(lines) - 1)
    x = 112 if portrait else 150
    center_y = 1260 if portrait else 940
    y = center_y - total_height / 2
    for line, line_height in zip(lines, line_heights):
        draw.text(
            (x, y),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=stroke_width,
            stroke_fill=(8, 12, 18, 238),
        )
        y += line_height + line_gap


def _fit_single_line_font(draw, text, max_width, start_size, minimum_size):
    for font_size in range(start_size, minimum_size - 1, -2):
        font = _load_font(font_size)
        if _text_size(draw, text, font)[0] <= max_width:
            return font
    return _load_font(minimum_size)


def _fit_multiline_font(draw, lines, max_width, max_height, start_size):
    for font_size in range(start_size, 63, -4):
        font = _load_font(font_size)
        widths = [_text_size(draw, line, font)[0] for line in lines]
        heights = [_text_size(draw, line, font)[1] for line in lines]
        total_height = sum(heights) + max(12, font_size // 8) * (len(lines) - 1)
        if max(widths) <= max_width and total_height <= max_height:
            return font
    return _load_font(64)


def _delete_replaced_file(storage, old_name, current_name):
    if old_name and old_name != current_name:
        storage.delete(old_name)


def _fit_display_text(draw, text, max_width, start_size, minimum_size):
    font = _fit_single_line_font(draw, text, max_width, start_size, minimum_size)
    if _text_size(draw, text, font)[0] <= max_width:
        return text, font
    suffix = "..."
    display = text
    while display and _text_size(draw, display + suffix, font)[0] > max_width:
        display = display[:-1]
    return display + suffix, font


