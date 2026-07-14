from django.http import FileResponse, Http404, JsonResponse
from django.db.models import F
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from studio.models import (
    Character,
    Episode,
    GenerationTask,
    StoryboardShot,
    VideoAsset,
    VideoComposition,
)
from studio.repositories.workspace import CURRENT_WORKSPACE_ID, WorkspaceRepository
from studio.services.model_slots import (
    get_simple_model_slots,
    save_simple_model_slot,
    verify_simple_model_slot,
)
from studio.services.video import (
    bind_shot_characters,
    queue_episode_videos,
    queue_export,
    queue_shot_video,
    reorder_shots,
    save_video_prompt_override,
    storyboard_video_prompt,
    video_page_data,
)


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
            "error": error,
            "success": success,
            "active_nav": "system",
        },
    )

def video_page(request, workspace_id):
    return video_episode_page(request, workspace_id, 1)


def video_episode_page(request, workspace_id, episode_number):
    repository = WorkspaceRepository()
    workspace = repository.get_episode_workspace(workspace_id, episode_number)
    episode = _episode(workspace_id, episode_number)
    data = video_page_data(episode)
    characters = list(
        Character.objects.filter(script=episode.script)
        .prefetch_related("assets")
        .order_by("position", "id")
    )
    return render(
        request,
        "studio/video.html",
        {
            "workspace": workspace,
            "episode": episode,
            "shots": data["shots"],
            "counts": data["counts"],
            "composition": data["composition"],
            "characters": characters,
            "active_tab": request.GET.get("tab", "shots"),
            "active_nav": "video",
        },
    )


@require_POST
def generate_shot_video_view(request, workspace_id, episode_number, shot_id):
    shot = _shot(workspace_id, episode_number, shot_id)
    try:
        queue_shot_video(shot, force=request.POST.get("force") == "1")
    except ValueError as exc:
        return _video_error(request, workspace_id, episode_number, str(exc))
    return _video_redirect(workspace_id, episode_number)




@require_POST
def save_shot_video_prompt_view(request, workspace_id, episode_number, shot_id):
    shot = _shot(workspace_id, episode_number, shot_id)
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
        return _video_error(request, workspace_id, episode_number, str(exc))
    return _video_redirect(workspace_id, episode_number)


@require_POST
def batch_video_view(request, workspace_id, episode_number):
    episode = _episode(workspace_id, episode_number)
    _, errors = queue_episode_videos(episode, failed_only=request.POST.get("mode") == "failed")
    if errors:
        return _video_error(request, workspace_id, episode_number, "；".join(errors))
    return _video_redirect(workspace_id, episode_number)


@require_POST
def bind_shot_characters_view(request, workspace_id, episode_number, shot_id):
    shot = _shot(workspace_id, episode_number, shot_id)
    try:
        bind_shot_characters(shot, request.POST.getlist("character_ids"))
    except ValueError as exc:
        return _video_error(request, workspace_id, episode_number, str(exc))
    return _video_redirect(workspace_id, episode_number)


@require_POST
def reorder_video_shots_view(request, workspace_id, episode_number):
    episode = _episode(workspace_id, episode_number)
    try:
        reorder_shots(episode, [value for value in request.POST.get("shot_order", "").split(",") if value])
    except ValueError as exc:
        return _video_error(request, workspace_id, episode_number, str(exc), tab="assembly")
    return _video_redirect(workspace_id, episode_number, tab="assembly")


@require_POST
def export_video_view(request, workspace_id, episode_number):
    episode = _episode(workspace_id, episode_number)
    try:
        queue_export(episode)
    except ValueError as exc:
        return _video_error(request, workspace_id, episode_number, str(exc), tab="assembly")
    return _video_redirect(workspace_id, episode_number, tab="assembly")


@require_GET
def video_status_view(request, workspace_id, episode_number):
    episode = _episode(workspace_id, episode_number)
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
        }
    )


@require_GET
def download_shot_video_view(request, workspace_id, episode_number, video_id):
    asset = VideoAsset.objects.select_related("shot__storyboard__episode").filter(
        pk=video_id,
        shot__storyboard__project__workspace_id=workspace_id,
        shot__storyboard__episode__episode_number=episode_number,
        status=VideoAsset.STATUS_READY,
    ).first()
    if asset is None or not asset.video:
        raise Http404("镜头视频不存在。")
    asset.video.open("rb")
    return FileResponse(asset.video, as_attachment=True, filename=asset.video.name.rsplit("/", 1)[-1])


@require_GET
def download_composition_view(request, workspace_id, episode_number):
    composition = VideoComposition.objects.select_related("episode__script__project").filter(
        episode__script__project__workspace_id=workspace_id,
        episode__episode_number=episode_number,
        status=VideoComposition.STATUS_READY,
    ).first()
    if composition is None or not composition.video:
        raise Http404("成片尚未导出。")
    composition.video.open("rb")
    return FileResponse(
        composition.video,
        as_attachment=True,
        filename=f"episode-{episode_number}.mp4",
    )


def _episode(workspace_id, episode_number):
    try:
        return Episode.objects.select_related("script__project", "storyboard_prompt").get(
            script__project__workspace_id=workspace_id,
            episode_number=episode_number,
            script__project__selected_outline_id=F("script__outline_id"),
        )
    except Episode.DoesNotExist as exc:
        raise Http404("剧集不存在。") from exc


def _shot(workspace_id, episode_number, shot_id):
    try:
        return StoryboardShot.objects.select_related("storyboard__episode").get(
            shot_id=shot_id,
            storyboard__project__workspace_id=workspace_id,
            storyboard__episode__episode_number=episode_number,
        )
    except StoryboardShot.DoesNotExist as exc:
        raise Http404("分镜不存在。") from exc


def _video_redirect(workspace_id, episode_number, tab="shots"):
    url = reverse("studio:video_episode", args=[workspace_id, episode_number])
    return redirect(f"{url}?tab={tab}")


def _video_error(request, workspace_id, episode_number, error, tab="shots"):
    repository = WorkspaceRepository()
    workspace = repository.get_episode_workspace(workspace_id, episode_number)
    episode = _episode(workspace_id, episode_number)
    data = video_page_data(episode)
    return render(
        request,
        "studio/video.html",
        {
            "workspace": workspace,
            "episode": episode,
            "shots": data["shots"],
            "counts": data["counts"],
            "composition": data["composition"],
            "characters": Character.objects.filter(script=episode.script).prefetch_related("assets"),
            "active_tab": tab,
            "active_nav": "video",
            "error": error,
        },
        status=400,
    )
