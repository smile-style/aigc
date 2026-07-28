import logging

from django.db import transaction
from django.utils import timezone

from studio.models import (
    Character,
    Episode,
    EpisodeWorkflowRun,
    GenerationTask,
    StoryboardPrompt,
    SubtitleTrack,
    VideoAsset,
    VideoComposition,
)
from studio.repositories.workspace import WorkspaceRepository
from studio.services.characters import (
    create_storyboard_characters,
    storyboard_character_candidates,
)
from studio.services.subtitles import is_subtitle_stale, queue_subtitle_alignment
from studio.services.video import queue_episode_videos, queue_export, sync_episode_shots


logger = logging.getLogger(__name__)

MAX_AUTO_RETRIES = 3
MIN_AUTOMATIC_CHARACTER_SHOTS = 6
AUTO_RETRY_COUNT_KEY = "auto_retry_count"
AUTO_RETRY_MESSAGE_KEY = "auto_retry_message"
AUTO_RETRY_EXHAUSTED_KEY = "auto_retry_exhausted"

ACTIVE_RUN_STATUSES = {
    EpisodeWorkflowRun.STATUS_QUEUED,
    EpisodeWorkflowRun.STATUS_RUNNING,
}
ACTIVE_TASK_STATUSES = {
    GenerationTask.STATUS_PENDING,
    GenerationTask.STATUS_RUNNING,
    GenerationTask.STATUS_RETRY_WAIT,
}
FAILED_TASK_STATUSES = {
    GenerationTask.STATUS_FAILED,
    GenerationTask.STATUS_CANCELLED,
}
STAGE_PROGRESS = {
    EpisodeWorkflowRun.STAGE_SCRIPT: 5,
    EpisodeWorkflowRun.STAGE_STORYBOARD: 15,
    EpisodeWorkflowRun.STAGE_CHARACTERS: 30,
    EpisodeWorkflowRun.STAGE_VIDEOS: 42,
    EpisodeWorkflowRun.STAGE_CLEAN_EXPORT: 74,
    EpisodeWorkflowRun.STAGE_SUBTITLES: 84,
    EpisodeWorkflowRun.STAGE_CAPTIONED_EXPORT: 93,
    EpisodeWorkflowRun.STAGE_COMPLETE: 100,
}
STAGE_LABELS = {
    EpisodeWorkflowRun.STAGE_SCRIPT: "生成本集剧本",
    EpisodeWorkflowRun.STAGE_CHARACTERS: "生成新增角色资产",
    EpisodeWorkflowRun.STAGE_STORYBOARD: "生成分镜",
    EpisodeWorkflowRun.STAGE_VIDEOS: "生成镜头视频",
    EpisodeWorkflowRun.STAGE_CLEAN_EXPORT: "拼接无字幕成片",
    EpisodeWorkflowRun.STAGE_SUBTITLES: "生成字幕",
    EpisodeWorkflowRun.STAGE_CAPTIONED_EXPORT: "导出有字幕成片",
    EpisodeWorkflowRun.STAGE_COMPLETE: "本集成片已完成",
}
STAGES = list(STAGE_PROGRESS)


def create_episode_workflow(episode):
    with transaction.atomic():
        episode = Episode.objects.select_for_update().get(pk=episode.pk)
        active = episode.workflow_runs.filter(status__in=ACTIVE_RUN_STATUSES).first()
        if active:
            return active, False
        run = EpisodeWorkflowRun.objects.create(
            episode=episode,
            status=EpisodeWorkflowRun.STATUS_RUNNING,
            stage=EpisodeWorkflowRun.STAGE_SCRIPT,
            progress_percent=STAGE_PROGRESS[EpisodeWorkflowRun.STAGE_SCRIPT],
            started_at=timezone.now(),
        )
    try:
        run = advance_episode_workflow(run.id)
    except Exception as exc:
        logger.exception("Could not start episode workflow %s", run.id)
        _fail_run(run.id, exc)
        run = EpisodeWorkflowRun.objects.get(pk=run.id)
    return run, True


def retry_episode_workflow(run):
    with transaction.atomic():
        run = EpisodeWorkflowRun.objects.select_for_update().get(pk=run.pk)
        if run.status != EpisodeWorkflowRun.STATUS_FAILED:
            raise ValueError("只有失败的自动生产任务可以继续。")
        run.status = EpisodeWorkflowRun.STATUS_RUNNING
        run.child_task = None
        run.error_message = ""
        run.finished_at = None
        details = dict(run.details or {})
        for key in (
            AUTO_RETRY_COUNT_KEY,
            AUTO_RETRY_MESSAGE_KEY,
            AUTO_RETRY_EXHAUSTED_KEY,
        ):
            details.pop(key, None)
        details = _prepare_stage_retry(run, details)
        run.details = details
        run.save(
            update_fields=[
                "status",
                "child_task",
                "error_message",
                "finished_at",
                "details",
                "updated_at",
            ]
        )
    try:
        return advance_episode_workflow(run.id)
    except Exception as exc:
        logger.exception("Could not retry episode workflow %s", run.id)
        _fail_run(run.id, exc)
        return EpisodeWorkflowRun.objects.get(pk=run.id)


def advance_active_workflows(limit=20):
    run_ids = list(
        EpisodeWorkflowRun.objects.filter(status__in=ACTIVE_RUN_STATUSES)
        .order_by("created_at", "id")
        .values_list("id", flat=True)[:limit]
    )
    for run_id in run_ids:
        try:
            advance_episode_workflow(run_id)
        except Exception as exc:
            logger.exception("Could not advance episode workflow %s", run_id)
            _fail_run(run_id, exc)


def advance_episode_workflow(run_id):
    for _ in range(12):
        with transaction.atomic():
            run = (
                EpisodeWorkflowRun.objects.select_for_update()
                .select_related("episode__script__project", "child_task")
                .get(pk=run_id)
            )
            if run.status not in ACTIVE_RUN_STATUSES:
                return run
            if run.status == EpisodeWorkflowRun.STATUS_QUEUED:
                run.status = EpisodeWorkflowRun.STATUS_RUNNING
                run.started_at = run.started_at or timezone.now()

            if run.child_task_id:
                child = GenerationTask.objects.get(pk=run.child_task_id)
                if child.status in ACTIVE_TASK_STATUSES:
                    _save_run(run)
                    return run
                if child.status in FAILED_TASK_STATUSES:
                    _mark_failed(run, child.error_message or "子任务执行失败。")
                    _save_run(run)
                    return run
                details = dict(run.details or {})
                details[f"{child.task_type}_complete"] = True
                if child.task_type == GenerationTask.TYPE_CHARACTER_PROFILE:
                    result = child.result_snapshot or {}
                    details["character_names"] = list(result.get("character_names") or [])
                    details["character_new_names"] = list(
                        result.get("new_character_names") or []
                    )
                run.details = details
                run.child_task = None

            handler = STAGE_HANDLERS[run.stage]
            should_continue = handler(run)
            _save_run(run)
            if not should_continue:
                return run
    return EpisodeWorkflowRun.objects.get(pk=run_id)


def workflow_payload(run):
    run = EpisodeWorkflowRun.objects.select_related("episode__script__project").get(pk=run.pk)
    details = dict(run.details or {})
    auto_retry_count = int(details.get(AUTO_RETRY_COUNT_KEY) or 0)
    auto_retry_message = str(details.get(AUTO_RETRY_MESSAGE_KEY) or "")
    auto_retrying = run.status in ACTIVE_RUN_STATUSES and auto_retry_count > 0
    current_index = STAGES.index(run.stage)
    steps = []
    for index, stage in enumerate(STAGES[:-1]):
        if run.status == EpisodeWorkflowRun.STATUS_SUCCEEDED or index < current_index:
            status = "succeeded"
        elif index == current_index:
            status = "failed" if run.status == EpisodeWorkflowRun.STATUS_FAILED else "running"
        else:
            status = "pending"
        steps.append({"key": stage, "label": STAGE_LABELS[stage], "status": status})
    return {
        "id": run.id,
        "status": run.status,
        "stage": run.stage,
        "stage_label": STAGE_LABELS[run.stage],
        "progress_percent": run.progress_percent,
        "error_message": run.error_message,
        "auto_retry_count": auto_retry_count,
        "auto_retry_max": MAX_AUTO_RETRIES,
        "auto_retry_message": auto_retry_message,
        "auto_retrying": auto_retrying,
        "details": details,
        "steps": steps,
        "episode_number": run.episode.episode_number,
        "workspace_id": run.episode.script.project.workspace_id,
        "script_id": run.episode.script_id,
        "terminal": run.status
        in {
            EpisodeWorkflowRun.STATUS_SUCCEEDED,
            EpisodeWorkflowRun.STATUS_FAILED,
            EpisodeWorkflowRun.STATUS_CANCELLED,
        },
    }


def _handle_script(run):
    episode = run.episode
    if episode.full_script.strip():
        return _move_to(run, EpisodeWorkflowRun.STAGE_STORYBOARD)
    task_data = WorkspaceRepository().create_episode_script_task(
        episode.script.project.workspace_id,
        episode.episode_number,
        script_id=episode.script_id,
    )
    run.child_task_id = task_data["id"]
    run.progress_percent = 8
    return False


def _handle_characters(run):
    episode = run.episode
    details = dict(run.details or {})
    storyboard = StoryboardPrompt.objects.filter(episode=episode).first()
    if not storyboard or not storyboard.prompts_payload:
        return _move_to(run, EpisodeWorkflowRun.STAGE_STORYBOARD)
    sync_episode_shots(episode)
    candidates = storyboard_character_candidates(
        episode.script,
        episode_number=episode.episode_number,
        include_existing=True,
    )
    selected = [
        item
        for item in candidates
        if item["shot_count"] >= MIN_AUTOMATIC_CHARACTER_SHOTS
    ]
    details["character_profile_complete"] = True
    details["character_names"] = [item["name"] for item in selected]
    details["character_shot_counts"] = {
        item["name"]: item["shot_count"] for item in selected
    }
    details["character_shot_threshold"] = MIN_AUTOMATIC_CHARACTER_SHOTS
    if "character_image_task_ids" not in details:
        existing_names = set(
            Character.objects.filter(script=episode.script, is_deleted=False).values_list(
                "name", flat=True
            )
        )
        missing_names = [
            item["name"]
            for item in selected
            if item["name"] not in existing_names
        ]
        if missing_names:
            create_storyboard_characters(episode.script, missing_names)

    characters_query = Character.objects.filter(script=episode.script, is_deleted=False)
    if "character_names" in details:
        characters_query = characters_query.filter(name__in=details["character_names"])
    characters = list(characters_query.prefetch_related("assets"))
    new_characters = [character for character in characters if not character.assets.exists()]
    reused = len(characters) - len(new_characters)
    task_ids = list(details.get("character_image_task_ids") or [])
    if "character_image_task_ids" not in details:
        repository = WorkspaceRepository()
        for character in new_characters:
            task_data = repository.create_character_image_task(
                episode.script.project.workspace_id,
                character.id,
                character.image_prompt,
            )
            task_ids.append(task_data["id"])
        details["character_image_task_ids"] = task_ids
    details.setdefault("character_new_total", len(new_characters))
    details.setdefault("character_reused", reused)
    details["character_total"] = len(task_ids)
    run.details = details

    tasks = list(GenerationTask.objects.filter(pk__in=task_ids))
    failed = next((task for task in tasks if task.status in FAILED_TASK_STATUSES), None)
    if failed:
        _mark_failed(run, failed.error_message or "角色图片生成失败。")
        return False
    ready = sum(task.status == GenerationTask.STATUS_SUCCEEDED for task in tasks)
    details["character_ready"] = ready
    run.details = details
    total = len(task_ids)
    run.progress_percent = 32 + int((ready / total) * 8) if total else 40
    if ready < total:
        return False
    return _move_to(run, EpisodeWorkflowRun.STAGE_VIDEOS)


def _handle_storyboard(run):
    episode = run.episode
    storyboard = StoryboardPrompt.objects.filter(episode=episode).first()
    if storyboard and storyboard.prompts_payload:
        return _move_to(run, EpisodeWorkflowRun.STAGE_CHARACTERS)
    task_data = WorkspaceRepository().create_storyboard_task(
        episode.script.project.workspace_id,
        episode.episode_number,
        script_id=episode.script_id,
    )
    run.child_task_id = task_data["id"]
    run.progress_percent = 18
    return False


def _handle_videos(run):
    episode = run.episode
    shots = sync_episode_shots(episode)
    if not shots:
        _mark_failed(run, "分镜中没有可生成的视频镜头。")
        return False
    latest = [shot.video_assets.first() for shot in shots]
    failed = next(
        (asset for asset in latest if asset and asset.status == VideoAsset.STATUS_FAILED),
        None,
    )
    if failed:
        details = dict(run.details or {})
        if details.pop("retry_failed_videos", False):
            run.details = details
            queue_episode_videos(episode, failed_only=True)
            return False
        _mark_failed(run, failed.error_message or "镜头视频生成失败。")
        return False
    ready = sum(
        bool(
            shot.video_assets.filter(
                is_selected=True, status=VideoAsset.STATUS_READY
            ).first()
        )
        for shot in shots
    )
    details = dict(run.details or {})
    details.update({"video_ready": ready, "video_total": len(shots)})
    run.details = details
    run.progress_percent = 42 + int((ready / len(shots)) * 30)
    if ready == len(shots):
        return _move_to(run, EpisodeWorkflowRun.STAGE_CLEAN_EXPORT)
    _, errors = queue_episode_videos(episode)
    if errors:
        _mark_failed(run, errors[0])
    return False


def _handle_clean_export(run):
    return _handle_export(
        run,
        VideoComposition.VARIANT_CLEAN,
        False,
        EpisodeWorkflowRun.STAGE_SUBTITLES,
        78,
    )


def _handle_subtitles(run):
    episode = run.episode
    track = SubtitleTrack.objects.filter(episode=episode).first()
    if track and track.cues.exists() and not is_subtitle_stale(track):
        from studio.services.subtitle_qc import ensure_subtitle_qc

        qc_result = ensure_subtitle_qc(track)
        details = dict(run.details or {})
        details["subtitle_qc"] = {
            "status": track.qc_status,
            "score": track.qc_score,
            "reason": track.qc_reason,
        }
        run.details = details
        if qc_result.passed:
            return _move_to(run, EpisodeWorkflowRun.STAGE_CAPTIONED_EXPORT)
        details["captioned_export_skipped"] = True
        run.details = details
        return _move_to(run, EpisodeWorkflowRun.STAGE_COMPLETE)
    if (
        track
        and track.aligned_at
        and not track.cues.exists()
        and not is_subtitle_stale(track)
    ):
        details = dict(run.details or {})
        details["subtitle_qc"] = {
            "status": track.qc_status,
            "score": track.qc_score,
            "reason": track.qc_reason,
        }
        details["captioned_export_skipped"] = True
        details["no_spoken_dialogue"] = True
        run.details = details
        return _move_to(run, EpisodeWorkflowRun.STAGE_COMPLETE)

    active = episode.script.project.generation_tasks.filter(
        task_type=GenerationTask.TYPE_SUBTITLE_ALIGN,
        status__in=ACTIVE_TASK_STATUSES,
    ).filter(input_snapshot__episode_id=episode.id).first()
    if active:
        run.child_task = active
        run.progress_percent = 87
        return False
    task, _ = queue_subtitle_alignment(episode)
    run.child_task = task
    run.progress_percent = 87
    return False


def _handle_captioned_export(run):
    return _handle_export(
        run,
        VideoComposition.VARIANT_CAPTIONED,
        True,
        EpisodeWorkflowRun.STAGE_COMPLETE,
        96,
    )


def _handle_complete(run):
    run.status = EpisodeWorkflowRun.STATUS_SUCCEEDED
    run.progress_percent = 100
    run.finished_at = timezone.now()
    run.error_message = ""
    return False


def _handle_export(run, variant, include_subtitles, next_stage, waiting_progress):
    episode = run.episode
    ready = episode.video_compositions.filter(
        variant=variant,
        status=VideoComposition.STATUS_READY,
    ).first()
    if ready:
        details = dict(run.details or {})
        details[f"{variant}_composition_id"] = ready.id
        run.details = details
        return _move_to(run, next_stage)
    exporting = episode.video_compositions.filter(
        variant=variant,
        status=VideoComposition.STATUS_EXPORTING,
    ).first()
    if exporting:
        task = GenerationTask.objects.filter(
            task_type=GenerationTask.TYPE_VIDEO_EXPORT,
            target_id=str(exporting.id),
        ).first()
        if task and task.status in FAILED_TASK_STATUSES:
            _mark_failed(run, task.error_message or "成片导出失败。")
            return False
        if task:
            run.child_task = task
        run.progress_percent = waiting_progress
        return False
    task, _ = queue_export(episode, include_subtitles=include_subtitles)
    run.child_task = task
    run.progress_percent = waiting_progress
    return False


def _move_to(run, stage):
    run.details = _clear_auto_retry(dict(run.details or {}))
    run.stage = stage
    run.progress_percent = STAGE_PROGRESS[stage]
    run.child_task = None
    run.error_message = ""
    return True


def _mark_failed(run, message):
    message = str(message)
    details = dict(run.details or {})
    retry_count = int(details.get(AUTO_RETRY_COUNT_KEY) or 0)
    if retry_count < MAX_AUTO_RETRIES:
        retry_count += 1
        details[AUTO_RETRY_COUNT_KEY] = retry_count
        details[AUTO_RETRY_MESSAGE_KEY] = message
        details.pop(AUTO_RETRY_EXHAUSTED_KEY, None)
        run.details = _prepare_stage_retry(run, details)
        run.status = EpisodeWorkflowRun.STATUS_RUNNING
        run.child_task = None
        run.error_message = ""
        run.finished_at = None
        return run

    details[AUTO_RETRY_MESSAGE_KEY] = message
    details[AUTO_RETRY_EXHAUSTED_KEY] = True
    run.details = details
    run.status = EpisodeWorkflowRun.STATUS_FAILED
    run.error_message = message
    run.finished_at = timezone.now()
    return run


def _prepare_stage_retry(run, details):
    if run.stage == EpisodeWorkflowRun.STAGE_CHARACTERS:
        details.pop("character_image_task_ids", None)
        details.pop("character_ready", None)
    elif run.stage == EpisodeWorkflowRun.STAGE_VIDEOS:
        details["retry_failed_videos"] = True
    return details


def _clear_auto_retry(details):
    for key in (
        AUTO_RETRY_COUNT_KEY,
        AUTO_RETRY_MESSAGE_KEY,
        AUTO_RETRY_EXHAUSTED_KEY,
    ):
        details.pop(key, None)
    return details


def _fail_run(run_id, error):
    try:
        with transaction.atomic():
            run = (
                EpisodeWorkflowRun.objects.select_for_update()
                .filter(pk=run_id, status__in=ACTIVE_RUN_STATUSES)
                .first()
            )
            if not run:
                return None
            _mark_failed(run, error)
            _save_run(run)
            return run
    except Exception:
        logger.exception("Could not persist workflow retry state for %s", run_id)
        return None


def _save_run(run):
    run.progress_percent = max(0, min(100, int(run.progress_percent)))
    run.save()


STAGE_HANDLERS = {
    EpisodeWorkflowRun.STAGE_SCRIPT: _handle_script,
    EpisodeWorkflowRun.STAGE_CHARACTERS: _handle_characters,
    EpisodeWorkflowRun.STAGE_STORYBOARD: _handle_storyboard,
    EpisodeWorkflowRun.STAGE_VIDEOS: _handle_videos,
    EpisodeWorkflowRun.STAGE_CLEAN_EXPORT: _handle_clean_export,
    EpisodeWorkflowRun.STAGE_SUBTITLES: _handle_subtitles,
    EpisodeWorkflowRun.STAGE_CAPTIONED_EXPORT: _handle_captioned_export,
    EpisodeWorkflowRun.STAGE_COMPLETE: _handle_complete,
}
