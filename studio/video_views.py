import shutil
import tempfile
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

from django.db import transaction
from django.db.models import Count, F, Max
from django.db.models.deletion import ProtectedError
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from studio.models import (
    Character,
    Episode,
    EpisodeWorkflowRun,
    GenerationTask,
    PublishingAccount,
    PublishingTask,
    ShotSubtitleSetting,
    StoryboardShot,
    SubtitleTrack,
    VideoAsset,
    VideoComposition,
)
from studio.repositories.workspace import CURRENT_WORKSPACE_ID, WorkspaceRepository
from studio.services.characters import create_storyboard_characters, storyboard_character_candidates
from studio.services.episode_workflow import workflow_payload
from studio.services.model_slots import (
    get_simple_model_slots,
    save_simple_model_slot,
    verify_simple_model_slot,
)
from studio.services.subtitles import (
    queue_subtitle_alignment,
    render_srt,
    save_shot_subtitle,
    save_subtitle_style,
    save_subtitle_track,
    subtitle_page_data,
    subtitle_snapshot,
    subtitle_status_data,
)
from studio.services.video import (
    bind_shot_characters,
    import_external_captioned_video,
    queue_episode_videos,
    queue_export,
    queue_shot_video,
    reorder_shots,
    save_video_prompt_override,
    storyboard_video_prompt,
    sync_latest_character_assets,
    video_page_data,
)
from studio.video_models import script_storage_key


def system_settings_page(request):
    error = None
    success = None
    if request.method == "POST":
        action = request.POST.get("action") or request.GET.get("action")
        category = request.POST.get("category", "")
        try:
            if action not in {"save_slot", "verify_slot"}:
                raise ValueError("无法识别的系统管理操作。")
            model = save_simple_model_slot(category, request.POST)
            if action == "save_slot":
                success = f"{model.name}已保存。"
            else:
                model = verify_simple_model_slot(category)
                if model.verification_status == model.VERIFICATION_FAILED:
                    error = f"{model.name}验证失败：{model.verification_message}"
                else:
                    success = f"{model.name}验证完成。"
        except Exception as exc:
            error = str(exc)

    try:
        workspace = WorkspaceRepository().get_current_workspace()
    except FileNotFoundError:
        workspace = None

    return render(
        request,
        "studio/system_settings.html",
        {
            "workspace": workspace,
            "slot_rows": get_simple_model_slots(),
            "publishing_accounts": PublishingAccount.objects.exclude(
                platform=PublishingAccount.PLATFORM_DOUYIN
            ),
            "publishing_success": request.GET.get("publishing_success", ""),
            "publishing_error": request.GET.get("publishing_error", ""),
            "error": error,
            "success": success,
            "active_nav": "system",
        },
    )

def finished_films_page(request):
    composition_base = (
        VideoComposition.objects.filter(status=VideoComposition.STATUS_READY)
        .exclude(video="")
    )
    project_options = list(
        composition_base.values(
            "episode__script__outline_id",
            "episode__script__outline__title",
        )
        .annotate(
            latest_activity=Max("updated_at"),
            latest_composition_id=Max("id"),
        )
        .order_by("-latest_activity", "-latest_composition_id", "episode__script__outline_id")
    )
    selected_project = request.GET.get("project", "").strip()
    valid_project_ids = {
        str(row["episode__script__outline_id"]) for row in project_options
    }
    if selected_project not in valid_project_ids:
        selected_project = (
            str(project_options[0]["episode__script__outline_id"])
            if project_options
            else ""
        )

    selected_compositions = (
        composition_base.filter(episode__script__outline_id=selected_project)
        if selected_project
        else composition_base.none()
    )
    compositions = list(
        selected_compositions
        .select_related(
            "episode__cover",
            "episode__script__outline",
            "episode__script__project",
        )
        .order_by(
            "episode__script_id",
            "episode__episode_number",
            "-version",
            "-id",
        )
    )
    task_map = {}
    for task in PublishingTask.objects.filter(
        composition_id__in=[item.id for item in compositions]
    ).select_related("account").order_by("-created_at", "-id"):
        by_platform = task_map.setdefault(task.composition_id, {})
        by_platform.setdefault(task.platform, task)
    for composition in compositions:
        latest_tasks = list(task_map.get(composition.id, {}).values())
        composition.latest_publishing_tasks = latest_tasks
        composition.latest_publishing_task = latest_tasks[0] if latest_tasks else None
        package_metadata = _douyin_package_metadata(composition)
        composition.douyin_title = package_metadata["title"]
        composition.douyin_description = package_metadata["description"]
        composition.douyin_tags = package_metadata["tags"]
    publishing_accounts = PublishingAccount.objects.filter(
        status=PublishingAccount.STATUS_CONNECTED
    ).exclude(platform=PublishingAccount.PLATFORM_DOUYIN)
    script_ids = {item.episode.script_id for item in compositions}
    episode_totals = {
        row["script_id"]: row["total"]
        for row in (
            Episode.objects.filter(script_id__in=script_ids)
            .values("script_id")
            .annotate(total=Count("id"))
        )
    }

    projects = {}
    for composition in compositions:
        episode = composition.episode
        script = episode.script
        outline = script.outline
        project = projects.setdefault(
            outline.pk,
            {
                "project_id": outline.pk,
                "workspace_id": script.project.workspace_id,
                "title": outline.title,
                "total_episodes": episode_totals.get(script.id, 0),
                "episodes": {},
                "latest_exported": None,
            },
        )
        exported_at = composition.exported_at or composition.updated_at
        if project["latest_exported"] is None or exported_at > project["latest_exported"]:
            project["latest_exported"] = exported_at
        episode_item = project["episodes"].get(episode.episode_number)
        if episode_item is None:
            project["episodes"][episode.episode_number] = {
                "number": episode.episode_number,
                "title": episode.title,
                "latest": composition,
                "history": [],
            }
        else:
            episode_item["history"].append(composition)

    publishing_groups = {
        "publishing": {
            PublishingTask.STATUS_QUEUED,
            PublishingTask.STATUS_RUNNING,
            PublishingTask.STATUS_RETRY_WAIT,
            PublishingTask.STATUS_SUBMITTED,
        },
        "attention": {
            PublishingTask.STATUS_REJECTED,
            PublishingTask.STATUS_OUTCOME_UNKNOWN,
            PublishingTask.STATUS_FAILED,
        },
    }
    state_labels = {
        "unpublished": "\u5f85\u53d1\u5e03",
        "publishing": "\u53d1\u5e03\u4e2d",
        "published": "\u5df2\u53d1\u5e03",
        "attention": "\u9700\u5904\u7406",
    }
    for project in projects.values():
        for episode_item in project["episodes"].values():
            tasks = episode_item["latest"].latest_publishing_tasks
            statuses = {task.status for task in tasks}
            if statuses and statuses == {PublishingTask.STATUS_PUBLISHED}:
                state = "published"
            elif statuses & publishing_groups["publishing"]:
                state = "publishing"
            elif statuses & publishing_groups["attention"]:
                state = "attention"
            else:
                state = "unpublished"
            episode_item["publishing_state"] = state
            episode_item["publishing_state_label"] = state_labels[state]

    project_options = [
        {
            "id": row["episode__script__outline_id"],
            "title": row["episode__script__outline__title"],
        }
        for row in project_options
    ]
    all_episode_items = [
        episode
        for project in projects.values()
        for episode in project["episodes"].values()
    ]
    film_stats = {
        "ready": len(all_episode_items),
        "unpublished": sum(item["publishing_state"] == "unpublished" for item in all_episode_items),
        "publishing": sum(item["publishing_state"] == "publishing" for item in all_episode_items),
        "published": sum(item["publishing_state"] == "published" for item in all_episode_items),
        "attention": sum(item["publishing_state"] == "attention" for item in all_episode_items),
    }

    query = request.GET.get("q", "").strip()[:80]
    selected_status = request.GET.get("status", "").strip()
    selected_sort = request.GET.get("sort", "episode_desc").strip()
    if selected_status not in {"", *state_labels}:
        selected_status = ""
    if selected_sort not in {"episode_asc", "episode_desc"}:
        selected_sort = "episode_desc"
    project_rows = []
    for project in projects.values():
        if selected_project and str(project["project_id"]) != selected_project:
            continue
        episodes = list(project["episodes"].values())
        if query:
            normalized_query = query.casefold()
            episodes = [
                item
                for item in episodes
                if normalized_query in project["title"].casefold()
                or normalized_query in item["title"].casefold()
                or normalized_query in str(item["number"])
            ]
        if selected_status:
            episodes = [item for item in episodes if item["publishing_state"] == selected_status]
        if not episodes:
            continue
        if selected_sort == "episode_asc":
            episodes.sort(key=lambda item: item["number"])
        else:
            episodes.sort(key=lambda item: item["number"], reverse=True)
        project["episodes"] = episodes
        project["ready_count"] = len(project["episodes"])
        project["ready_percent"] = round(
            project["ready_count"] / project["total_episodes"] * 100
        ) if project["total_episodes"] else 0
        project_rows.append(project)
    project_rows.sort(
        key=lambda item: item["latest_exported"].timestamp() if item["latest_exported"] else 0,
        reverse=True,
    )
    return render(
        request,
        "studio/films.html",
        {
            "film_projects": project_rows,
            "film_count": len(compositions),
            "filtered_count": sum(len(project["episodes"]) for project in project_rows),
            "film_stats": film_stats,
            "project_options": project_options,
            "filters": {
                "q": query,
                "project": selected_project,
                "status": selected_status,
                "sort": selected_sort,
            },
            "publishing_accounts": publishing_accounts,
            "film_notice": request.GET.get("notice", ""),
            "film_error": request.GET.get("error", ""),
            "publishing_task_id": request.GET.get("publishing_task", ""),
            "active_nav": "films",
        },
    )


@require_POST
def delete_finished_film_view(request, composition_id):
    composition = (
        VideoComposition.objects.select_related("episode__script__outline")
        .filter(pk=composition_id, status=VideoComposition.STATUS_READY)
        .exclude(video="")
        .first()
    )
    if composition is None:
        raise Http404("Finished film not found")

    project_id = composition.episode.script.outline_id
    redirect_url = reverse("studio:finished_films")
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = ""

    def delete_redirect(query):
        return redirect(next_url or f"{redirect_url}?{query}")

    latest_id = (
        VideoComposition.objects.filter(
            episode=composition.episode,
            status=VideoComposition.STATUS_READY,
        )
        .exclude(video="")
        .order_by("-version", "-id")
        .values_list("id", flat=True)
        .first()
    )
    if composition.id == latest_id:
        query = urlencode(
            {"project": project_id, "error": "\u5f53\u524d\u6700\u65b0\u7248\u672c\u4e0d\u80fd\u5220\u9664\u3002"}
        )
        return delete_redirect(query)
    if composition.publishing_tasks.exists():
        query = urlencode(
            {"project": project_id, "error": "\u8be5\u7248\u672c\u5b58\u5728\u53d1\u5e03\u8bb0\u5f55\uff0c\u4e0d\u80fd\u5220\u9664\u3002"}
        )
        return delete_redirect(query)

    storage = composition.video.storage
    file_name = composition.video.name
    try:
        with transaction.atomic():
            composition.delete()
            transaction.on_commit(lambda: storage.delete(file_name))
    except ProtectedError:
        query = urlencode(
            {"project": project_id, "error": "\u8be5\u7248\u672c\u6b63\u5728\u88ab\u5176\u4ed6\u8bb0\u5f55\u4f7f\u7528\uff0c\u4e0d\u80fd\u5220\u9664\u3002"}
        )
        return delete_redirect(query)
    query = urlencode(
        {"project": project_id, "notice": "\u5386\u53f2\u7248\u672c\u5df2\u5220\u9664\u3002"}
    )
    return delete_redirect(query)


@require_GET
def download_finished_film_view(request, composition_id):
    composition = (
        VideoComposition.objects.select_related("episode__script__outline")
        .filter(
            pk=composition_id,
            status=VideoComposition.STATUS_READY,
        )
        .exclude(video="")
        .first()
    )
    if composition is None or not composition.video:
        raise Http404("Finished film not found")
    composition.video.open("rb")
    return FileResponse(
        composition.video,
        as_attachment=True,
        filename=(
            f"{script_storage_key(composition.episode.script)}-"
            f"EP{composition.episode.episode_number:03d}-"
            f"FINAL-v{composition.version:03d}.mp4"
        ),
    )


@require_GET
def download_douyin_package_view(request, composition_id):
    composition = (
        VideoComposition.objects.select_related(
            "episode__cover",
            "episode__script__outline",
        )
        .filter(pk=composition_id, status=VideoComposition.STATUS_READY)
        .exclude(video="")
        .first()
    )
    if composition is None or not composition.video:
        raise Http404("Finished film not found")

    episode = composition.episode
    metadata = _douyin_package_metadata(composition)
    prefix = f"EP{episode.episode_number:03d}"
    package = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    with zipfile.ZipFile(package, mode="w", allowZip64=True) as archive:
        video_suffix = _safe_media_suffix(composition.video.name, ".mp4")
        with composition.video.open("rb") as source:
            with archive.open(
                f"{prefix}-video{video_suffix}", mode="w", force_zip64=True
            ) as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)

        cover = getattr(episode, "cover", None)
        if cover and cover.image:
            cover_suffix = _safe_media_suffix(cover.image.name, ".jpg")
            with cover.image.open("rb") as source:
                with archive.open(f"{prefix}-cover{cover_suffix}", mode="w") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)

        copy_text = (
            f"标题：{metadata['title']}\n\n"
            f"简介：\n{metadata['description']}\n\n"
            f"标签：{metadata['tags']}\n"
        )
        archive.writestr(f"{prefix}-发布文案.txt", copy_text.encode("utf-8"))

    package.seek(0)
    return FileResponse(
        package,
        as_attachment=True,
        content_type="application/zip",
        filename=(
            f"{script_storage_key(episode.script)}-"
            f"{prefix}-douyin-package.zip"
        ),
    )


def _douyin_package_metadata(composition):
    episode = composition.episode
    return {
        "title": episode.title.strip() or f"第 {episode.episode_number} 集",
        "description": episode.summary.strip(),
        "tags": "#漫剧 #AI动画 #短剧",
    }


def _safe_media_suffix(name, default):
    suffix = Path(str(name or "")).suffix.lower()
    if suffix in {".mp4", ".mov", ".jpg", ".jpeg", ".png", ".webp"}:
        return suffix
    return default


def video_page(request, workspace_id):
    return video_episode_page(request, workspace_id, 1)


def video_episode_page(request, workspace_id, episode_number):
    return _render_video_episode(request, workspace_id, episode_number)


def video_script_episode_page(request, workspace_id, script_id, episode_number):
    return _render_video_episode(request, workspace_id, episode_number, script_id=script_id)


def _render_video_episode(request, workspace_id, episode_number, script_id=None):
    repository = WorkspaceRepository()
    workspace = repository.get_episode_workspace(workspace_id, episode_number, script_id=script_id)
    episode = _episode(workspace_id, episode_number, script_id=script_id)
    episode.number = episode.episode_number
    has_synced_shots = StoryboardShot.objects.filter(storyboard__episode=episode).exists()
    data = video_page_data(episode, sync=not has_synced_shots)
    characters = list(
        Character.objects.filter(script=episode.script)
        .filter(is_deleted=False)
        .prefetch_related("assets")
        .order_by("position", "id")
    )
    shot_subtitle_auto_open = request.GET.get("shot_subtitle", "")
    for shot in data["shots"]:
        shot.subtitle_auto_open = str(shot.shot_id) == shot_subtitle_auto_open
    unknown_characters = storyboard_character_candidates(episode.script)
    active_character_tasks = [
        task
        for task in GenerationTask.objects.filter(
            project=episode.script.project,
            task_type=GenerationTask.TYPE_CHARACTER_IMAGE,
            status__in=[
                GenerationTask.STATUS_PENDING,
                GenerationTask.STATUS_RUNNING,
                GenerationTask.STATUS_RETRY_WAIT,
            ],
        )
        if (task.input_snapshot or {}).get("script_id") == episode.script_id
    ]
    return render(
        request,
        "studio/video.html",
        {
            "workspace": workspace,
            "episode": episode,
            "shots": data["shots"],
            "counts": data["counts"],
            "composition": data["composition"],
            "clean_composition": data["clean_composition"],
            "captioned_composition": data["captioned_composition"],
            "composition_versions": data["composition_versions"],
            "episode_workflow": _latest_workflow_payload(episode),
            "subtitle": subtitle_page_data(episode, data["shots"]),
            "publishing_accounts": PublishingAccount.objects.filter(
                status=PublishingAccount.STATUS_CONNECTED
            ),
            "asset_updates_available": data["asset_updates_available"],
            "sync_result": request.GET.get("synced"),
            "characters": characters,
            "active_tab": request.GET.get("tab", "shots"),
            "subtitle_auto_open": request.GET.get("subtitle") == "1",
            "subtitle_style_auto_open": request.GET.get("subtitle_style") == "1",
            "active_nav": "video",
            "unknown_characters": unknown_characters,
            "active_character_tasks": active_character_tasks,
            "characters_queued": request.GET.get("characters_queued", ""),
        },
    )


@require_POST
def generate_shot_video_view(request, workspace_id, episode_number, shot_id):
    script_id = _request_script_id(request)
    shot = _shot(workspace_id, episode_number, shot_id, script_id=script_id)
    try:
        queue_shot_video(shot, force=request.POST.get("force") == "1")
    except ValueError as exc:
        return _video_error(request, workspace_id, episode_number, str(exc), script_id=script_id)
    return _video_redirect(workspace_id, episode_number, script_id=script_id)




@require_POST
def save_shot_video_prompt_view(request, workspace_id, episode_number, shot_id):
    script_id = _request_script_id(request)
    shot = _shot(workspace_id, episode_number, shot_id, script_id=script_id)
    try:
        action = request.POST.get("action", "save")
        if action == "reset":
            prompt = storyboard_video_prompt(shot)
        else:
            prompt = request.POST.get("prompt", "")
        save_video_prompt_override(shot, prompt)
        if action == "save_and_generate":
            queue_shot_video(shot, force=shot.video_assets.exists())
    except ValueError as exc:
        return _video_error(request, workspace_id, episode_number, str(exc), script_id=script_id)
    return _video_redirect(workspace_id, episode_number, script_id=script_id)


@require_POST
def batch_video_view(request, workspace_id, episode_number):
    script_id = _request_script_id(request)
    episode = _episode(workspace_id, episode_number, script_id=script_id)
    _, errors = queue_episode_videos(episode, failed_only=request.POST.get("mode") == "failed")
    if errors:
        return _video_error(request, workspace_id, episode_number, "；".join(errors))
    return _video_redirect(workspace_id, episode_number, script_id=script_id)

@require_POST
def sync_character_assets_view(request, workspace_id, episode_number):
    script_id = _request_script_id(request)
    episode = _episode(workspace_id, episode_number, script_id=script_id)
    updated = sync_latest_character_assets(episode)
    if script_id:
        url = reverse("studio:video_script_episode", args=[workspace_id, script_id, episode_number])
    else:
        url = reverse("studio:video_episode", args=[workspace_id, episode_number])
    return redirect(f"{url}?synced={updated}")



@require_POST
def bind_shot_characters_view(request, workspace_id, episode_number, shot_id):
    script_id = _request_script_id(request)
    shot = _shot(workspace_id, episode_number, shot_id, script_id=script_id)
    try:
        bind_shot_characters(shot, request.POST.getlist("character_ids"))
    except ValueError as exc:
        return _video_error(request, workspace_id, episode_number, str(exc), script_id=script_id)
    return _video_redirect(workspace_id, episode_number, script_id=script_id)


@require_POST
def reorder_video_shots_view(request, workspace_id, episode_number):
    script_id = _request_script_id(request)
    episode = _episode(workspace_id, episode_number, script_id=script_id)
    try:
        reorder_shots(episode, [value for value in request.POST.get("shot_order", "").split(",") if value])
    except ValueError as exc:
        return _video_error(request, workspace_id, episode_number, str(exc), tab="assembly", script_id=script_id)
    return _video_redirect(workspace_id, episode_number, tab="assembly", script_id=script_id)


@require_POST
def generate_subtitles_view(request, workspace_id, episode_number):
    script_id = _request_script_id(request)
    episode = _episode(workspace_id, episode_number, script_id=script_id)
    try:
        queue_subtitle_alignment(episode)
    except ValueError as exc:
        return _video_error(
            request,
            workspace_id,
            episode_number,
            str(exc),
            tab="assembly",
            script_id=script_id,
            subtitle_auto_open=True,
        )
    return _video_redirect(
        workspace_id,
        episode_number,
        tab="assembly",
        script_id=script_id,
        subtitle=True,
    )


@require_POST
def generate_shot_subtitles_view(request, workspace_id, episode_number, shot_id):
    script_id = _request_script_id(request)
    shot = _shot(workspace_id, episode_number, shot_id, script_id=script_id)
    episode = shot.storyboard.episode
    tab = "assembly" if request.POST.get("return_tab") == "assembly" else "shots"
    try:
        queue_subtitle_alignment(episode, shot=shot)
    except ValueError as exc:
        return _video_error(
            request,
            workspace_id,
            episode_number,
            str(exc),
            tab=tab,
            script_id=script_id,
            shot_subtitle_auto_open=str(shot.shot_id),
        )
    return _video_redirect(
        workspace_id,
        episode_number,
        tab=tab,
        script_id=script_id,
        shot_subtitle=shot.shot_id,
    )


@require_POST
def save_shot_subtitles_view(request, workspace_id, episode_number, shot_id):
    script_id = _request_script_id(request)
    shot = _shot(workspace_id, episode_number, shot_id, script_id=script_id)
    episode = shot.storyboard.episode
    tab = "assembly" if request.POST.get("return_tab") == "assembly" else "shots"
    track = SubtitleTrack.objects.filter(episode=episode).first()
    if track is None:
        raise Http404("Subtitle track not found")
    setting, _ = ShotSubtitleSetting.objects.get_or_create(shot=shot)
    try:
        cues = []
        for cue_id in request.POST.getlist("cue_id"):
            cues.append(
                {
                    "id": cue_id,
                    "text": request.POST.get(f"cue_text_{cue_id}", ""),
                    "start_ms": _seconds_to_ms(request.POST.get(f"cue_start_{cue_id}")),
                    "end_ms": _seconds_to_ms(request.POST.get(f"cue_end_{cue_id}")),
                    "reviewed": request.POST.get(f"cue_reviewed_{cue_id}") == "1",
                }
            )
        save_shot_subtitle(
            track,
            setting,
            enabled=request.POST.get("enabled") == "1",
            offset_ms=int(request.POST.get("offset_ms") or 0),
            cues=cues,
            confirm_all=request.POST.get("action") == "confirm_all",
        )
    except (TypeError, ValueError, InvalidOperation) as exc:
        return _video_error(
            request,
            workspace_id,
            episode_number,
            str(exc),
            tab=tab,
            script_id=script_id,
            shot_subtitle_auto_open=str(shot.shot_id),
        )
    return _video_redirect(
        workspace_id,
        episode_number,
        tab=tab,
        script_id=script_id,
        shot_subtitle=shot.shot_id,
    )

@require_POST
def save_subtitles_view(request, workspace_id, episode_number):
    script_id = _request_script_id(request)
    episode = _episode(workspace_id, episode_number, script_id=script_id)
    track = SubtitleTrack.objects.filter(episode=episode).first()
    if track is None:
        raise Http404("Subtitle track not found")
    try:
        cues = []
        for cue_id in request.POST.getlist("cue_id"):
            cues.append(
                {
                    "id": cue_id,
                    "text": request.POST.get(f"cue_text_{cue_id}", ""),
                    "start_ms": _seconds_to_ms(request.POST.get(f"cue_start_{cue_id}")),
                    "end_ms": _seconds_to_ms(request.POST.get(f"cue_end_{cue_id}")),
                    "reviewed": request.POST.get(f"cue_reviewed_{cue_id}") == "1",
                }
            )
        save_subtitle_track(
            track,
            enabled=request.POST.get("enabled") == "1",
            global_offset_ms=int(request.POST.get("global_offset_ms") or 0),
            cues=cues,
            confirm_all=request.POST.get("action") == "confirm_all",
        )
    except (TypeError, ValueError, InvalidOperation) as exc:
        return _video_error(
            request,
            workspace_id,
            episode_number,
            str(exc),
            tab="assembly",
            script_id=script_id,
            subtitle_auto_open=True,
        )
    return _video_redirect(workspace_id, episode_number, tab="assembly", script_id=script_id, subtitle=True)


@require_POST
def save_subtitle_style_view(request, workspace_id, episode_number):
    script_id = _request_script_id(request)
    episode = _episode(workspace_id, episode_number, script_id=script_id)
    tab = "assembly" if request.POST.get("return_tab") == "assembly" else "shots"
    track, _ = SubtitleTrack.objects.get_or_create(episode=episode)
    try:
        save_subtitle_style(
            track,
            {
                "font_name": request.POST.get("font_name"),
                "font_size": request.POST.get("font_size") or 38,
                "text_color": request.POST.get("text_color"),
                "outline_color": request.POST.get("outline_color"),
                "outline_size": request.POST.get("outline_size") or 3,
                "margin_bottom": request.POST.get("margin_bottom") or 92,
            },
        )
    except (TypeError, ValueError) as exc:
        return _video_error(
            request,
            workspace_id,
            episode_number,
            str(exc),
            tab=tab,
            script_id=script_id,
            subtitle_style_auto_open=True,
        )
    return _video_redirect(
        workspace_id,
        episode_number,
        tab=tab,
        script_id=script_id,
    )


@require_POST
def export_video_view(request, workspace_id, episode_number):
    script_id = _request_script_id(request)
    episode = _episode(workspace_id, episode_number, script_id=script_id)
    try:
        variant = request.POST.get("variant")
        include_subtitles = None
        if variant in {VideoComposition.VARIANT_CLEAN, VideoComposition.VARIANT_CAPTIONED}:
            include_subtitles = variant == VideoComposition.VARIANT_CAPTIONED
        queue_export(episode, include_subtitles=include_subtitles)
    except ValueError as exc:
        return _video_error(request, workspace_id, episode_number, str(exc), tab="assembly", script_id=script_id)
    return _video_redirect(workspace_id, episode_number, tab="assembly", script_id=script_id)


@require_POST
def upload_external_captioned_video_view(request, workspace_id, episode_number):
    script_id = _request_script_id(request)
    episode = _episode(workspace_id, episode_number, script_id=script_id)
    try:
        composition = import_external_captioned_video(
            episode,
            request.FILES.get("video"),
        )
    except ValueError as exc:
        return _video_error(
            request,
            workspace_id,
            episode_number,
            str(exc),
            tab="assembly",
            script_id=script_id,
            external_upload_auto_open=True,
        )
    return _video_redirect(
        workspace_id,
        episode_number,
        tab="assembly",
        script_id=script_id,
        external_uploaded=composition.version,
    )


@require_GET
def video_status_view(request, workspace_id, episode_number):
    script_id = _request_script_id(request)
    episode = _episode(workspace_id, episode_number, script_id=script_id)
    data = video_page_data(episode, sync=False)
    return JsonResponse(
        {
            "counts": data["counts"],
            "shots": [
                {
                    "shot_id": str(shot.shot_id),
                    "status": shot.latest_video.status if shot.latest_video else "idle",
                    "error": shot.latest_video.error_message if shot.latest_video else "",
                }
                for shot in data["shots"]
            ],
            "composition_status": data["composition"].status if data["composition"] else "draft",
            "subtitle": subtitle_status_data(episode),
        }
    )


@require_GET
def download_shot_video_view(request, workspace_id, episode_number, video_id):
    asset = VideoAsset.objects.select_related("shot__storyboard__episode__script__outline").filter(
        pk=video_id,
        shot__storyboard__project__workspace_id=workspace_id,
        shot__storyboard__episode__episode_number=episode_number,
        status=VideoAsset.STATUS_READY,
    ).first()
    if asset is None or not asset.video:
        raise Http404("镜头视频不存在。")
    asset.video.open("rb")
    episode = asset.shot.storyboard.episode
    filename = (
        f"{script_storage_key(episode.script)}-EP{episode.episode_number:03d}-"
        f"SH{asset.shot.shot_number:03d}-v{asset.version:03d}.mp4"
    )
    return FileResponse(asset.video, as_attachment=True, filename=filename)


@require_GET
def download_composition_view(request, workspace_id, episode_number):
    variant = request.GET.get("variant", VideoComposition.VARIANT_CLEAN)
    if variant not in {VideoComposition.VARIANT_CLEAN, VideoComposition.VARIANT_CAPTIONED}:
        raise Http404("Unknown composition variant")
    script_id = _request_script_id(request)
    queryset = VideoComposition.objects.select_related("episode__script__project", "episode__script__outline").filter(
        episode__script__project__workspace_id=workspace_id,
        episode__episode_number=episode_number,
        status=VideoComposition.STATUS_READY,
        variant=variant,
    )
    if script_id:
        queryset = queryset.filter(episode__script_id=script_id)
    composition = queryset.first()
    if composition is None or not composition.video:
        raise Http404("成片尚未导出。")
    composition.video.open("rb")
    return FileResponse(
        composition.video,
        as_attachment=True,
        filename=(
            f"{script_storage_key(composition.episode.script)}-EP{episode_number:03d}-"
            f"{'CAPTIONED' if variant == VideoComposition.VARIANT_CAPTIONED else 'CLEAN'}"
            f"-v{composition.version:03d}.mp4"
        ),
    )


@require_GET
def download_subtitles_view(request, workspace_id, episode_number):
    script_id = _request_script_id(request)
    episode = _episode(workspace_id, episode_number, script_id=script_id)
    track = SubtitleTrack.objects.filter(episode=episode, enabled=True).first()
    if track is None or not track.cues.exists():
        raise Http404("Subtitle track not found")
    response = HttpResponse(
        render_srt(subtitle_snapshot(track)),
        content_type="application/x-subrip; charset=utf-8",
    )
    filename = f"{script_storage_key(episode.script)}-EP{episode_number:03d}.srt"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _seconds_to_ms(value):
    seconds = Decimal(str(value or "0"))
    if seconds < 0:
        raise ValueError("Subtitle time cannot be negative")
    return int(seconds * 1000)


def _latest_workflow_payload(episode):
    run = EpisodeWorkflowRun.objects.filter(episode=episode).first()
    return workflow_payload(run) if run else None


def _request_script_id(request):
    value = request.POST.get("script_id") or request.GET.get("script_id")
    return int(value) if value else None


def _episode(workspace_id, episode_number, script_id=None):
    try:
        queryset = Episode.objects.select_related("script__project", "script__outline", "storyboard_prompt")
        filters = {"script__project__workspace_id": workspace_id, "episode_number": episode_number}
        if script_id:
            filters["script_id"] = script_id
        else:
            filters["script__project__selected_outline_id"] = F("script__outline_id")
        return queryset.get(**filters)
    except Episode.DoesNotExist as exc:
        raise Http404("剧集不存在。") from exc


def _shot(workspace_id, episode_number, shot_id, script_id=None):
    try:
        filters = {
            "shot_id": shot_id,
            "storyboard__project__workspace_id": workspace_id,
            "storyboard__episode__episode_number": episode_number,
        }
        if script_id:
            filters["storyboard__script_id"] = script_id
        return StoryboardShot.objects.select_related("storyboard__episode").get(**filters)
    except StoryboardShot.DoesNotExist as exc:
        raise Http404("分镜不存在。") from exc


def _video_redirect(
    workspace_id,
    episode_number,
    tab="shots",
    script_id=None,
    subtitle=False,
    shot_subtitle=None,
    external_uploaded=None,
):
    if script_id:
        url = reverse("studio:video_script_episode", args=[workspace_id, script_id, episode_number])
    else:
        url = reverse("studio:video_episode", args=[workspace_id, episode_number])
    query = [f"tab={tab}"]
    if subtitle:
        query.append("subtitle=1")
    if shot_subtitle:
        query.append(f"shot_subtitle={shot_subtitle}")
    if external_uploaded:
        query.append(f"external_uploaded={external_uploaded}")
    return redirect(f"{url}?{'&'.join(query)}")


def _video_error(
    request,
    workspace_id,
    episode_number,
    error,
    tab="shots",
    script_id=None,
    subtitle_auto_open=False,
    subtitle_style_auto_open=False,
    shot_subtitle_auto_open="",
    external_upload_auto_open=False,
):
    script_id = script_id or _request_script_id(request)
    repository = WorkspaceRepository()
    workspace = repository.get_episode_workspace(workspace_id, episode_number, script_id=script_id)
    episode = _episode(workspace_id, episode_number, script_id=script_id)
    data = video_page_data(episode, sync=False)
    subtitle_data = subtitle_page_data(episode, data["shots"])
    for shot in data["shots"]:
        shot.subtitle_auto_open = str(shot.shot_id) == str(shot_subtitle_auto_open)
    return render(
        request,
        "studio/video.html",
        {
            "workspace": workspace,
            "episode": episode,
            "shots": data["shots"],
            "counts": data["counts"],
            "composition": data["composition"],
            "clean_composition": data["clean_composition"],
            "captioned_composition": data["captioned_composition"],
            "composition_versions": data["composition_versions"],
            "episode_workflow": _latest_workflow_payload(episode),
            "subtitle": subtitle_data,
            "characters": Character.objects.filter(
                script=episode.script,
                is_deleted=False,
            ).prefetch_related("assets"),
            "active_tab": tab,
            "subtitle_auto_open": subtitle_auto_open,
            "subtitle_style_auto_open": subtitle_style_auto_open,
            "external_upload_auto_open": external_upload_auto_open,
            "active_nav": "video",
            "error": error,
        },
        status=400,
    )


@require_POST
def generate_storyboard_characters_view(request, workspace_id, episode_number):
    script_id = _request_script_id(request)
    episode = _episode(workspace_id, episode_number, script_id=script_id)
    try:
        tasks = create_storyboard_characters(
            episode.script,
            request.POST.getlist("character_names"),
        )
    except ValueError as exc:
        return _video_error(
            request,
            workspace_id,
            episode_number,
            str(exc),
            script_id=script_id,
        )
    url = _video_redirect_url(workspace_id, episode_number, script_id=script_id)
    return redirect(f"{url}?characters_queued={len(tasks)}")


def _video_redirect_url(workspace_id, episode_number, script_id=None):
    if script_id:
        return reverse(
            "studio:video_script_episode",
            args=[workspace_id, script_id, episode_number],
        )
    return reverse("studio:video_episode", args=[workspace_id, episode_number])
