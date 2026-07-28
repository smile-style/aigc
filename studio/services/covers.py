import hashlib
import os
import re
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max
from PIL import Image, ImageDraw, ImageFont, ImageOps

from studio.models import (
    CoverTemplate,
    CoverTemplateVersion,
    Episode,
    EpisodeCover,
    GenerationTask,
    Project,
    Script,
)


COVER_WIDTH = 1600
COVER_HEIGHT = 1000
COVER_GENERATION_SIZE = "1536x1024"
DEFAULT_COVER_TITLE_MIN_CHARACTERS = 6
MAX_COVER_TITLE_CHARACTERS = 10


def normalize_cover_title(value):
    compact = "".join(character for character in str(value or "").strip() if character.isalnum())
    if not compact or len(compact) > MAX_COVER_TITLE_CHARACTERS:
        raise ValueError("封面标题不能为空，且最多 10 个有效字符。")
    return compact


def default_episode_cover_title(episode):
    episode_title = "".join(
        character for character in str(episode.title or "") if character.isalnum()
    )
    if len(episode_title) >= DEFAULT_COVER_TITLE_MIN_CHARACTERS:
        return episode_title[:MAX_COVER_TITLE_CHARACTERS]

    candidates = [
        (episode.plan_payload or {}).get("next_crisis"),
        episode.cliffhanger,
        (episode.plan_payload or {}).get("resolution_or_reversal"),
        episode.key_conflict,
        episode.summary,
    ]
    units = episode_title
    seen_candidates = set()
    for candidate in candidates:
        compact = "".join(
            character for character in str(candidate or "") if character.isalnum()
        )
        if not compact or compact in seen_candidates:
            continue
        seen_candidates.add(compact)
        if not units and len(compact) >= DEFAULT_COVER_TITLE_MIN_CHARACTERS:
            return compact[:MAX_COVER_TITLE_CHARACTERS]
        for character in compact:
            if len(units) >= DEFAULT_COVER_TITLE_MIN_CHARACTERS:
                break
            if character not in units:
                units += character
    fallback = "危机突然提前降临"
    for character in fallback:
        if len(units) >= DEFAULT_COVER_TITLE_MIN_CHARACTERS:
            break
        units += character
    return units[:MAX_COVER_TITLE_CHARACTERS]


def build_cover_prompt(script):
    outline = script.outline
    characters = list(script.characters.filter(is_deleted=False).order_by("position", "id")[:2])
    character_context = "；".join(
        f"{item.name}：{item.appearance}，服装为{item.costume}" for item in characters
    )
    visual_style = "精致国漫插画、干净线稿、电影级光影"
    if script.character_visual_style == Script.CHARACTER_STYLE_REALISTIC:
        visual_style = "电影级偏真人质感、真实皮肤与自然人体结构"
    return (
        "为短视频漫剧生成一张 16:10 横版系列封面母版。"
        f"作品《{outline.title}》，核心设定：{outline.core_premise}。"
        f"主角设定：{outline.protagonist}。"
        + (f"角色外观锁定：{character_context}。" if character_context else "")
        + f"视觉风格：{visual_style}。"
        "画面只出现一到两名核心人物，人物集中在画面右侧，面部清晰、情绪强烈，"
        "用关键危机道具和环境体现冲突；左侧与下方保留干净、高对比的标题区域。"
        "构图适合手机信息流缩略图，主体轮廓明确，色彩层次清楚。"
        "无文字、无字母、无数字、无标志、无水印、无边框。"
    )


def split_cover_title(title):
    normalized = normalize_cover_title(title)
    if len(normalized) <= 7:
        return [normalized]
    split_at = (len(normalized) + 1) // 2
    return [normalized[:split_at], normalized[split_at:]]


@transaction.atomic
def save_cover_template(script, image_result, prompt_snapshot):
    template, _ = CoverTemplate.objects.select_for_update().get_or_create(
        script=script,
        defaults={
            "prompt_snapshot": prompt_snapshot,
            "model": image_result.model,
            "source_url": image_result.source_url,
            "version": 1,
        },
    )
    latest_version = template.versions.aggregate(value=Max("version"))["value"] or 0
    next_version = latest_version + 1
    extension = image_result.extension if image_result.extension in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
    filename = f"master-v{next_version:03d}{extension}"
    style_payload = {
        "canvas": [COVER_WIDTH, COVER_HEIGHT],
        "title_anchor": "bottom_left",
        "title_color": "#FFFFFF",
        "accent_color": "#FFCA3A",
    }
    version = CoverTemplateVersion(
        template=template,
        prompt_snapshot=prompt_snapshot,
        source_url=image_result.source_url,
        model=image_result.model,
        style_payload=style_payload,
        version=next_version,
    )
    version.background.save(filename, ContentFile(image_result.content), save=False)
    version.save()

    template.version = next_version
    template.prompt_snapshot = prompt_snapshot
    template.model = image_result.model
    template.source_url = image_result.source_url
    template.style_payload = style_payload
    template.background = version.background.name
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
    template.source_url = selected.source_url
    template.model = selected.model
    template.style_payload = selected.style_payload
    template.save()
    _render_all_episode_covers(template)
    return template


def _render_all_episode_covers(template):
    for episode in template.script.episodes.order_by("episode_number"):
        existing = getattr(episode, "cover", None)
        title_customized = bool(existing and existing.title_customized)
        title = (
            existing.title
            if title_customized
            else default_episode_cover_title(episode)
        )
        render_episode_cover(
            template, episode, title, title_customized=title_customized
        )


@transaction.atomic
def render_episode_cover(template, episode, title, *, title_customized=False):
    normalized_title = normalize_cover_title(title)
    if episode.script_id != template.script_id:
        raise ValueError("单集和封面母版不属于同一部作品。")

    template.background.open("rb")
    try:
        source = Image.open(template.background)
        canvas = ImageOps.fit(
            source.convert("RGB"),
            (COVER_WIDTH, COVER_HEIGHT),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    finally:
        template.background.close()

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle((0, 560, COVER_WIDTH, COVER_HEIGHT), fill=(10, 14, 20, 176))
    overlay_draw.rectangle((92, 605, 112, 905), fill=(255, 202, 58, 255))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(canvas)
    lines = split_cover_title(normalized_title)
    font = _fit_title_font(draw, lines)
    stroke_width = max(5, font.size // 20)
    line_gap = max(12, font.size // 8)
    line_heights = [_text_size(draw, line, font)[1] for line in lines]
    total_height = sum(line_heights) + line_gap * (len(lines) - 1)
    y = 760 - total_height / 2
    for line, line_height in zip(lines, line_heights):
        draw.text(
            (150, y),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=stroke_width,
            stroke_fill=(8, 12, 18, 238),
        )
        y += line_height + line_gap

    badge_font = _load_font(48)
    badge = f"EP {episode.episode_number:02d}"
    badge_bbox = draw.textbbox((0, 0), badge, font=badge_font)
    badge_width = badge_bbox[2] - badge_bbox[0]
    draw.rounded_rectangle((100, 76, 150 + badge_width, 148), radius=8, fill=(230, 57, 70, 238))
    draw.text((125, 87), badge, font=badge_font, fill=(255, 255, 255, 255))

    buffer = BytesIO()
    canvas.convert("RGB").save(buffer, format="JPEG", quality=92, optimize=True)
    digest = hashlib.sha1(f"{template.version}:{normalized_title}".encode("utf-8")).hexdigest()[:10]
    filename = f"episode-{episode.episode_number:03d}-v{template.version:03d}-{digest}.jpg"
    cover, _ = EpisodeCover.objects.select_for_update().get_or_create(
        episode=episode,
        defaults={
            "template": template,
            "title": normalized_title,
            "title_customized": title_customized,
        },
    )
    old_image = cover.image.name if cover.image else ""
    cover.template = template
    cover.title = normalized_title
    cover.title_customized = title_customized
    cover.template_version = template.version
    cover.image.save(filename, ContentFile(buffer.getvalue()), save=False)
    cover.save()
    if old_image and old_image != cover.image.name:
        cover.image.storage.delete(old_image)
    return cover


def create_cover_generation_task(workspace_id, script_id, prompt):
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("封面提示词不能为空。")
    with transaction.atomic():
        project = Project.objects.select_for_update().get(workspace_id=workspace_id)
        script = _script_for_project(project, script_id)
        target_id = f"script:{script.id}"
        running = project.generation_tasks.filter(
            task_type=GenerationTask.TYPE_COVER_IMAGE,
            target_id=target_id,
            status__in=[
                GenerationTask.STATUS_PENDING,
                GenerationTask.STATUS_RUNNING,
                GenerationTask.STATUS_RETRY_WAIT,
            ],
        ).order_by("-created_at", "-id").first()
        if running:
            return running, False
        task = GenerationTask.objects.create(
            project=project,
            task_type=GenerationTask.TYPE_COVER_IMAGE,
            target_id=target_id,
            input_snapshot={
                "script_id": script.id,
                "project_id": script.outline_id,
                "prompt": prompt,
            },
        )
        return task, True


def episode_for_workspace(workspace_id, script_id, episode_number):
    project = Project.objects.get(workspace_id=workspace_id)
    script = _script_for_project(project, script_id)
    try:
        return script.episodes.select_related("script", "script__cover_template").get(
            episode_number=episode_number
        )
    except Episode.DoesNotExist as exc:
        raise FileNotFoundError(f"Episode not found: {episode_number}") from exc


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
    cover_map = {
        cover.episode_id: cover
        for cover in EpisodeCover.objects.filter(episode__script=script).select_related("episode")
    }
    episode_models = {
        episode.episode_number: episode
        for episode in script.episodes.all()
    }
    for episode_data in workspace.get("episodes", []):
        episode_model = episode_models.get(episode_data.get("episode"))
        cover = cover_map.get(episode_model.id) if episode_model else None
        automatic_title = (
            default_episode_cover_title(episode_model) if episode_model else ""
        )
        episode_data.update(
            {
                "cover_title": (
                    cover.title if cover and cover.title_customized else automatic_title
                ),
                "cover_image_url": cover.image.url if cover and cover.image else "",
                "has_cover": bool(cover and cover.image),
            }
        )
    latest_task = script.project.generation_tasks.filter(
        task_type=GenerationTask.TYPE_COVER_IMAGE,
        target_id=f"script:{script.id}",
    ).order_by("-created_at", "-id").first()
    workspace["cover_template"] = (
        {
            "id": template.id,
            "background_url": template.background.url,
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
    workspace["cover_prompt"] = template.prompt_snapshot if template else build_cover_prompt(script)
    return workspace


def _script_for_project(project, script_id):
    queryset = Script.objects.select_related("outline", "project").filter(project=project)
    if script_id:
        queryset = queryset.filter(pk=script_id)
    elif project.selected_outline_id:
        queryset = queryset.filter(outline_id=project.selected_outline_id)
    script = queryset.first()
    if script is None:
        raise FileNotFoundError("Script not found")
    return script


def _fit_title_font(draw, lines):
    for size in range(172, 79, -4):
        font = _load_font(size)
        widths = [_text_size(draw, line, font)[0] for line in lines]
        heights = [_text_size(draw, line, font)[1] for line in lines]
        if max(widths) <= 1320 and sum(heights) + max(12, size // 8) * (len(lines) - 1) <= 330:
            return font
    return _load_font(80)


def _text_size(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=max(2, font.size // 20))
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _load_font(size):
    configured = os.environ.get("COVER_FONT_PATH", "").strip()
    candidates = [
        configured,
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    raise ValueError(
        "找不到可用的中文封面字体，请通过 COVER_FONT_PATH 配置字体文件。"
    )
