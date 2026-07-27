from decimal import Decimal, InvalidOperation

from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.db.models import Count, F
from django.shortcuts import redirect, render
from django.urls import reverse
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
            "publishing_accounts": PublishingAccount.objects.all(),
            "publishing_success": request.GET.get("publishing_success", ""),
            "publishing_error": request.GET.get("publishing_error", ""),
            "error": error,
            "success": success,
            "active_nav": "system",
        },
    )

def finished_films_page(request):
    compositions = list(
        VideoComposition.objects.filter(status=VideoComposition.STATUS_READY)
        .exclude(video="")
        .select_related(
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
        task_map.setdefault(task.composition_id, task)
    for composition in compositions:
        composition.latest_publishing_task = task_map.get(composition.id)
    publishing_accounts = PublishingAccount.objects.filter(
        status=PublishingAccount.STATUS_CONNECTED
    )
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
            task = episode_item["latest"].latest_publishing_task
            if task and task.status == PublishingTask.STATUS_PUBLISHED:
                state = "published"
            elif task and task.status in publishing_groups["publishing"]:
                state = "publishing"
            elif task and task.status in publishing_groups["attention"]:
                state = "attention"
            else:
                state = "unpublished"
            episode_item["publishing_state"] = state
            episode_item["publishing_state_label"] = state_labels[state]

    all_projects = list(projects.values())
    project_options = [
        {"id": project["project_id"], "title": project["title"]}
        for project in all_projects
    ]
    all_episode_items = [
        episode
        for project in all_projects
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
    selected_project = request.GET.get("project", "").strip()
    selected_status = request.GET.get("status", "").strip()
    selected_sort = request.GET.get("sort", "recent").strip()
    if selected_status not in {"", *state_labels}:
        selected_status = ""
    if selected_sort not in {"recent", "episode_asc", "episode_desc"}:
        selected_sort = "recent"
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
        elif selected_sort == "episode_desc":
            episodes.sort(key=lambda item: item["number"], reverse=True)
        else:
            episodes.sort(
                key=lambda item: item["latest"].exported_at or item["latest"].updated_at,
                reverse=True,
            )
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
            "publishing_task_id": request.GET.get("publishing_task", ""),
            "active_nav": "films",
        },
    )


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
            "episode_workflow": _latest_workflow_payload(episode),
            "subtitle": subtitle_data,
            "characters": Character.objects.filter(
                script=episode.script,
                is_deleted=False,
            ).prefetch_related("assets"),
            "active_tab": tab,
            "subtitle_auto_open": subtitle_auto_open,
            "subtitle_style_auto_open": subtitle_style_auto_open,
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
