import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from studio.models import (
    Character,
    CharacterAsset,
    GenerationTask,
    ModelAssignment,
    ModelConfig,
    ShotCharacterReference,
    StoryboardPrompt,
    StoryboardShot,
    VideoAsset,
    VideoComposition,
)
from studio.services.model_config import assigned_model, ensure_default_video_models, video_provider_for


ACTIVE_VIDEO_STATUSES = {
    VideoAsset.STATUS_QUEUED,
    VideoAsset.STATUS_SUBMITTING,
    VideoAsset.STATUS_RUNNING,
    VideoAsset.STATUS_DOWNLOADING,
}
MAX_VIDEO_PROMPT_LENGTH = 5000
STORYBOARD_PROMPT_SECTIONS = (
    ("画面描述", "visual_description"),
    ("角色动作", "character_action"),
    ("对白 / 旁白", "dialogue_or_narration"),
    ("镜头语言", "camera_language"),
    ("图片 Prompt", "image_prompt"),
    ("视频 Prompt", "video_prompt"),
)


def sync_storyboard_shots(storyboard):
    characters = list(
        Character.objects.filter(script=storyboard.script).prefetch_related("assets").order_by("position", "id")
    )
    seen_numbers = []
    for position, payload in enumerate(storyboard.prompts_payload or [], start=1):
        shot_number = _as_int(payload.get("shot_number"), position)
        seen_numbers.append(shot_number)
        duration_label = str(payload.get("duration") or "")
        duration_seconds = _duration_seconds(payload.get("duration_seconds") or duration_label)
        names = payload.get("character_names")
        if not isinstance(names, list):
            names = _matching_character_names(payload, characters)
        names = [str(name).strip() for name in names if str(name).strip()]
        saved_position = (
            StoryboardShot.objects.filter(storyboard=storyboard, shot_number=shot_number)
            .values_list("position", flat=True).first()
        )
        shot, created = StoryboardShot.objects.update_or_create(
            storyboard=storyboard,
            shot_number=shot_number,
            defaults={
                "position": saved_position or position,
                "duration_seconds": duration_seconds,
                "duration_label": duration_label or f"{duration_seconds} 秒",
                "visual_description": str(payload.get("visual_description") or ""),
                "character_action": str(payload.get("character_action") or ""),
                "dialogue_or_narration": str(payload.get("dialogue_or_narration") or ""),
                "camera_language": str(payload.get("camera_language") or ""),
                "image_prompt": str(payload.get("image_prompt") or ""),
                "video_prompt": str(payload.get("video_prompt") or ""),
                "negative_prompt": str(payload.get("negative_prompt") or ""),
                "character_names": names,
            },
        )
        if created or not shot.character_references.exists():
            _bind_named_characters(shot, characters, names)
    storyboard.shots.exclude(shot_number__in=seen_numbers).delete()
    return list(storyboard.shots.all())


def sync_episode_shots(episode):
    try:
        storyboard = episode.storyboard_prompt
    except StoryboardPrompt.DoesNotExist:
        return []
    return sync_storyboard_shots(storyboard)


def bind_shot_characters(shot, character_ids):
    characters = list(
        Character.objects.filter(pk__in=character_ids, script=shot.storyboard.script)
        .prefetch_related("assets")
        .order_by("position", "id")[:5]
    )
    if len(characters) != len(set(character_ids)):
        missing = set(character_ids) - {str(character.id) for character in characters}
        if missing:
            raise ValueError("所选角色不存在或不属于当前剧本。")
    ShotCharacterReference.objects.filter(shot=shot).delete()
    names = []
    for position, character in enumerate(characters, start=1):
        asset = character.assets.first()
        if asset is None:
            raise ValueError(f"角色“{character.name}”还没有可用图片。")
        ShotCharacterReference.objects.create(
            shot=shot, character=character, asset=asset, position=position
        )
        names.append(character.name)
    shot.character_names = names
    shot.save(update_fields=["character_names", "updated_at"])


def sync_latest_character_assets(episode):
    references = (
        ShotCharacterReference.objects.filter(shot__storyboard__episode=episode)
        .select_related("character", "asset")
        .prefetch_related("character__assets")
    )
    updated = 0
    with transaction.atomic():
        for reference in references:
            latest = reference.character.assets.first()
            if latest is None or latest.id == reference.asset_id:
                continue
            reference.asset = latest
            reference.save(update_fields=["asset"])
            updated += 1
    return updated


def queue_shot_video(shot, force=False):
    active = shot.video_assets.filter(status__in=ACTIVE_VIDEO_STATUSES).first()
    if active:
        return active, False
    references = list(shot.character_references.select_related("character", "asset"))
    ensure_default_video_models()
    model = assigned_model(
        ModelAssignment.PURPOSE_SHOT_VIDEO,
        ModelConfig.CAPABILITY_VIDEO_REFERENCE,
    )
    if model is None:
        raise ValueError("系统管理中尚未配置分镜视频主模型。")
    source_prompt = storyboard_video_prompt(shot)
    prompt = effective_video_prompt(shot)
    is_custom_prompt = bool(shot.video_prompt_override.strip())
    parameters = dict(model.default_parameters or {})
    parameters["duration"] = shot.duration_seconds
    parameters["prompt_extend"] = False
    snapshot = {
        "shot_id": str(shot.shot_id),
        "shot_number": shot.shot_number,
        "model_id": model.model_id,
        "provider_type": model.provider.provider_type,
        "generation_mode": "reference" if references else "prompt_only",
        "prompt_policy": "manual_override" if is_custom_prompt else "full_storyboard_prompt",
        "source_storyboard_prompt": source_prompt,
        "source_storyboard_prompt_hash": _prompt_hash(source_prompt),
        "parameters": parameters,
        "references": [
            {
                "character_id": ref.character_id,
                "character_name": ref.character.name,
                "asset_id": ref.asset_id,
                "asset_version": ref.asset.version,
            }
            for ref in references
        ],
    }
    input_hash = hashlib.sha256(
        json.dumps({"prompt": prompt, **snapshot}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    version = (shot.video_assets.aggregate(value=Max("version"))["value"] or 0) + 1
    task = GenerationTask.objects.create(
        project=shot.storyboard.project,
        task_type=GenerationTask.TYPE_SHOT_VIDEO,
        target_id=str(shot.shot_id),
        input_snapshot={"video_asset_version": version, **snapshot},
    )
    asset = VideoAsset.objects.create(
        shot=shot,
        generation_task=task,
        model_config=model,
        version=version,
        prompt_snapshot=prompt,
        input_snapshot=snapshot,
        input_hash=input_hash,
    )
    return asset, True


def queue_episode_videos(episode, failed_only=False):
    queued = []
    errors = []
    for shot in sync_episode_shots(episode):
        latest = shot.video_assets.first()
        if failed_only and (latest is None or latest.status != VideoAsset.STATUS_FAILED):
            continue
        if not failed_only and latest and latest.status in ACTIVE_VIDEO_STATUSES | {VideoAsset.STATUS_READY}:
            continue
        try:
            asset, created = queue_shot_video(shot)
            if created:
                queued.append(asset)
        except ValueError as exc:
            errors.append(str(exc))
    return queued, errors


def process_video_asset(asset_id):
    asset = (
        VideoAsset.objects.select_related(
            "shot__storyboard__project", "model_config__provider", "generation_task"
        )
        .prefetch_related("shot__character_references__character", "shot__character_references__asset")
        .get(pk=asset_id)
    )
    task = asset.generation_task
    try:
        provider = video_provider_for(asset.model_config)
        try:
            if asset.status in {VideoAsset.STATUS_QUEUED, VideoAsset.STATUS_SUBMITTING}:
                asset.status = VideoAsset.STATUS_SUBMITTING
                asset.error_message = ""
                asset.save(update_fields=["status", "error_message", "updated_at"])
                _start_task(task)
                reference_ids = [
                    item["asset_id"]
                    for item in asset.input_snapshot.get("references", [])
                ]
                reference_map = {
                    item.id: item
                    for item in CharacterAsset.objects.filter(pk__in=reference_ids)
                }
                references = [reference_map[item_id] for item_id in reference_ids if item_id in reference_map]
                submission = provider.submit_video(
                    asset.prompt_snapshot,
                    [reference.image.path for reference in references],
                    parameters=asset.input_snapshot.get("parameters"),
                    negative_prompt=asset.shot.negative_prompt,
                )
                asset.provider_task_id = submission.task_id
                asset.provider_request_id = submission.request_id
                asset.status = VideoAsset.STATUS_RUNNING
                asset.save(
                    update_fields=["provider_task_id", "provider_request_id", "status", "updated_at"]
                )
                return asset
            if asset.status != VideoAsset.STATUS_RUNNING:
                return asset
            result = provider.get_task_result(asset.provider_task_id)
            if result.status in {"pending", "running"}:
                asset.result_snapshot = result.payload
                asset.save(update_fields=["result_snapshot", "updated_at"])
                return asset
            if result.status != "succeeded":
                raise RuntimeError(result.message or f"视频任务状态：{result.status}")
            video_url = result.video_url
            if not video_url:
                raise RuntimeError("视频任务成功但没有返回视频地址。")
            asset.status = VideoAsset.STATUS_DOWNLOADING
            asset.save(update_fields=["status", "updated_at"])
            content = provider.download(video_url)
            asset.video.save("result.mp4", ContentFile(content), save=False)
            asset.source_url = video_url
            asset.result_snapshot = result.payload
            asset.status = VideoAsset.STATUS_READY
            asset.finished_at = timezone.now()
            asset.is_selected = True
            with transaction.atomic():
                VideoAsset.objects.filter(shot=asset.shot, is_selected=True).exclude(pk=asset.pk).update(
                    is_selected=False
                )
                asset.save()
                _finish_task(task, {"video_asset_id": asset.id, "video_url": asset.video.url})
            return asset
        finally:
            provider.close()
    except Exception as exc:
        asset.status = VideoAsset.STATUS_FAILED
        asset.error_message = str(exc)
        asset.finished_at = timezone.now()
        asset.save(update_fields=["status", "error_message", "finished_at", "updated_at"])
        _fail_task(task, exc)
        return asset


def queue_export(episode):
    active = episode.script.project.generation_tasks.filter(
        task_type=GenerationTask.TYPE_VIDEO_EXPORT,
        status__in=[GenerationTask.STATUS_PENDING, GenerationTask.STATUS_RUNNING],
    ).filter(
        Q(input_snapshot__episode_id=episode.id)
        | Q(target_id=str(episode.id))
    ).first()
    if active:
        return active, False
    selected = [shot.video_assets.filter(is_selected=True, status=VideoAsset.STATUS_READY).first() for shot in sync_episode_shots(episode)]
    if not selected or any(asset is None for asset in selected):
        raise ValueError("所有分镜都生成并选定视频后才能导出。")
    version = (episode.video_compositions.aggregate(value=Max("version"))["value"] or 0) + 1
    composition = VideoComposition.objects.create(
        episode=episode,
        version=version,
        status=VideoComposition.STATUS_EXPORTING,
    )
    task = GenerationTask.objects.create(
        project=episode.script.project,
        task_type=GenerationTask.TYPE_VIDEO_EXPORT,
        target_id=str(composition.id),
        input_snapshot={
            "episode_id": episode.id,
            "composition_version": version,
            "video_asset_ids": [asset.id for asset in selected],
        },
    )
    return task, True


def process_export_task(task_id):
    task = GenerationTask.objects.select_related("project").get(pk=task_id)
    composition = VideoComposition.objects.select_related("episode__script__outline").get(pk=task.target_id)
    try:
        _start_task(task)
        assets = list(VideoAsset.objects.filter(pk__in=task.input_snapshot["video_asset_ids"]).select_related("shot"))
        asset_map = {asset.id: asset for asset in assets}
        ordered = [asset_map[asset_id] for asset_id in task.input_snapshot["video_asset_ids"]]
        content = _ffmpeg_concat(ordered)
        composition.video.save("result.mp4", ContentFile(content), save=False)
        composition.status = VideoComposition.STATUS_READY
        composition.error_message = ""
        composition.exported_at = timezone.now()
        composition.save()
        _finish_task(task, {"composition_id": composition.id, "video_url": composition.video.url})
    except Exception as exc:
        composition.status = VideoComposition.STATUS_FAILED
        composition.error_message = str(exc)
        composition.save(update_fields=["status", "error_message", "updated_at"])
        _fail_task(task, exc)
    return composition


def reorder_shots(episode, shot_ids):
    shots = {str(shot.shot_id): shot for shot in sync_episode_shots(episode)}
    if set(shot_ids) != set(shots):
        raise ValueError("排序列表与当前分镜不一致。")
    with transaction.atomic():
        for position, shot_id in enumerate(shot_ids, start=1):
            StoryboardShot.objects.filter(pk=shots[shot_id].pk).update(position=position)


def video_page_data(episode, sync=False):
    shots = (
        sync_episode_shots(episode)
        if sync
        else list(StoryboardShot.objects.filter(storyboard__episode=episode).order_by("position", "shot_number", "id"))
    )
    for shot in shots:
        shot.references_for_page = list(
            shot.character_references.select_related("character", "asset")
            .prefetch_related("character__assets")
        )
        shot.latest_video = shot.video_assets.first()
        shot.selected_video = shot.video_assets.filter(is_selected=True, status=VideoAsset.STATUS_READY).first()
        shot.storyboard_prompt_for_page = storyboard_video_prompt(shot)
        shot.effective_video_prompt = effective_video_prompt(shot)
        shot.has_prompt_override = bool(shot.video_prompt_override.strip())
        shot.prompt_source_changed = bool(
            shot.has_prompt_override
            and shot.video_prompt_override_source_hash != _prompt_hash(shot.storyboard_prompt_for_page)
        )
    composition = VideoComposition.objects.filter(episode=episode).first()
    asset_updates_available = sum(
        1
        for shot in shots
        for reference in shot.references_for_page
        if reference.character.assets.first()
        and reference.character.assets.first().id != reference.asset_id
    )
    return {
        "shots": shots,
        "composition": composition,
        "asset_updates_available": asset_updates_available,
        "counts": {
            "total": len(shots),
            "ready": sum(bool(shot.selected_video) for shot in shots),
            "running": sum(bool(shot.latest_video and shot.latest_video.status in ACTIVE_VIDEO_STATUSES) for shot in shots),
            "failed": sum(bool(shot.latest_video and shot.latest_video.status == VideoAsset.STATUS_FAILED) for shot in shots),
        },
    }


def _bind_named_characters(shot, characters, names):
    by_name = {character.name.strip().casefold(): character for character in characters}
    for position, name in enumerate(names[:5], start=1):
        character = by_name.get(name.casefold())
        if character is None:
            continue
        asset = character.assets.first()
        if asset:
            ShotCharacterReference.objects.get_or_create(
                shot=shot,
                character=character,
                defaults={"asset": asset, "position": position},
            )


def _matching_character_names(payload, characters):
    text = "\n".join(str(value) for value in payload.values() if isinstance(value, str))
    return [character.name for character in characters if character.name and character.name in text]




def storyboard_video_prompt(shot):
    return "\n\n".join(
        f"**{label}**\n{str(getattr(shot, field_name, '') or '').strip()}"
        for label, field_name in STORYBOARD_PROMPT_SECTIONS
    )


def effective_video_prompt(shot):
    prompt = shot.video_prompt_override.strip() or storyboard_video_prompt(shot)
    _validate_video_prompt(shot, prompt)
    return prompt


def save_video_prompt_override(shot, prompt):
    source_prompt = storyboard_video_prompt(shot)
    prompt = str(prompt or "").strip()
    _validate_video_prompt(shot, prompt)
    if prompt == source_prompt:
        shot.video_prompt_override = ""
        shot.video_prompt_override_source_hash = ""
    else:
        shot.video_prompt_override = prompt
        shot.video_prompt_override_source_hash = _prompt_hash(source_prompt)
    shot.save(
        update_fields=["video_prompt_override", "video_prompt_override_source_hash", "updated_at"]
    )


def _validate_video_prompt(shot, prompt):
    if not prompt:
        raise ValueError(f"镜头 {shot.shot_number} 的视频生成 Prompt 不能为空。")
    if len(prompt) > MAX_VIDEO_PROMPT_LENGTH:
        raise ValueError(
            f"镜头 {shot.shot_number} 的视频生成 Prompt 超过 {MAX_VIDEO_PROMPT_LENGTH} 个字符。"
        )


def _prompt_hash(prompt):
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _duration_seconds(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return max(2, min(15, value))
    match = re.search(r"\d+", str(value or ""))
    return max(2, min(15, int(match.group()))) if match else 5


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _start_task(task):
    if task is None:
        return
    GenerationTask.objects.filter(pk=task.pk).update(
        status=GenerationTask.STATUS_RUNNING,
        error_message="",
        started_at=task.started_at or timezone.now(),
        finished_at=None,
    )


def _finish_task(task, result):
    if task is None:
        return
    GenerationTask.objects.filter(pk=task.pk).update(
        status=GenerationTask.STATUS_SUCCEEDED,
        result_snapshot=result,
        error_message="",
        finished_at=timezone.now(),
    )


def _fail_task(task, error):
    if task is None:
        return
    GenerationTask.objects.filter(pk=task.pk).update(
        status=GenerationTask.STATUS_FAILED,
        error_message=str(error),
        finished_at=timezone.now(),
    )


def _ffmpeg_concat(assets):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 FFmpeg，请先安装 FFmpeg 后再导出。")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("未找到 FFprobe，请安装完整的 FFmpeg 后再导出。")
    with tempfile.TemporaryDirectory(prefix="aigc-video-") as temp_dir:
        temp = Path(temp_dir)
        normalized = []
        for index, asset in enumerate(assets, start=1):
            output = temp / f"normalized-{index:03d}.mp4"
            video_filter = (
                "scale=720:1280:force_original_aspect_ratio=decrease,"
                "pad=720:1280:(ow-iw)/2:(oh-ih)/2,fps=25"
            )
            has_audio = _has_audio_stream(ffprobe, asset.video.path)
            command = [ffmpeg, "-y", "-i", asset.video.path]
            if not has_audio:
                command.extend(
                    [
                        "-f", "lavfi", "-i",
                        "anullsrc=channel_layout=stereo:sample_rate=48000",
                    ]
                )
            command.extend(
                [
                    "-map", "0:v:0",
                    "-map", "0:a:0" if has_audio else "1:a:0",
                    "-vf", video_filter,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
                    "-af", "apad", "-shortest", "-movflags", "+faststart",
                    str(output),
                ]
            )
            _run_ffmpeg(command, cwd=temp)
            normalized.append(output)
        concat_file = temp / "concat.txt"
        concat_file.write_text("".join(f"file '{path.as_posix()}'\n" for path in normalized), encoding="utf-8")
        result = temp / "episode.mp4"
        _run_ffmpeg(
            [
                ffmpeg, "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat_file), "-c", "copy",
                "-movflags", "+faststart", str(result),
            ],
            cwd=temp,
        )
        return result.read_bytes()


def _has_audio_stream(ffprobe, video_path):
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=index", "-of", "csv=p=0", video_path,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return bool(result.stdout.strip())


def _run_ffmpeg(command, cwd=None):
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            timeout=300,
            cwd=cwd,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else str(exc)
        raise RuntimeError(f"FFmpeg 导出失败：{detail[-1200:]}") from exc
