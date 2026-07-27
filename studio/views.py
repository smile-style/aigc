import logging

from django.db import close_old_connections
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .constants import (
    EPISODE_COUNT,
    EPISODE_DURATION_LABEL,
    EPISODE_DURATION_MINUTES,
    GENRES,
)
from .llm.image_provider import ImageProvider
from .llm.provider import (
    LLMAPIError,
    LLMConfigurationError,
    LLMJSONParseError,
    LLMProvider,
)
from .models import EpisodeWorkflowRun, GenerationTask, ModelAssignment
from .services.episode_workflow import workflow_payload
from .repositories.workspace import CURRENT_WORKSPACE_ID, WorkspaceRepository
from .models import Character
from .services.characters import apply_character_visual_style, generate_character_profiles
from .services.covers import decorate_cover_workspace
from .services.model_config import image_provider_for, llm_provider_for
from .services.outline import generate_outlines
from .services.script import generate_episode_script, generate_script
from .services.storyboard import generate_storyboard

logger = logging.getLogger(__name__)

EXPECTED_GENERATION_ERRORS = (
    FileNotFoundError,
    LLMAPIError,
    LLMConfigurationError,
    LLMJSONParseError,
    ValueError,
)


def outline_page(request):
    repository = WorkspaceRepository()
    try:
        workspace = repository.get_current_workspace()
    except FileNotFoundError:
        workspace = None
    return _render_outline(request, workspace=workspace)


@require_POST
def generate_outlines_view(request):
    genre = request.POST.get("genre", "")
    repository = WorkspaceRepository()
    try:
        workspace = repository.get_current_workspace()
    except FileNotFoundError:
        workspace = None

    try:
        outlines = generate_outlines(llm_provider_for(ModelAssignment.PURPOSE_OUTLINE), genre)
        workspace = repository.replace_current_outline_set(genre, outlines)
    except EXPECTED_GENERATION_ERRORS as exc:
        return _render_outline(request, workspace=workspace, error=str(exc), genre=genre)

    return _render_outline(request, workspace=workspace, genre=genre)


@require_POST
def mark_outline_usable_view(request):
    repository = WorkspaceRepository()
    workspace_id = request.POST.get("workspace_id", CURRENT_WORKSPACE_ID)
    outline_id = request.POST.get("usable_outline_id") or request.POST.get("outline_id", "")
    try:
        repository.mark_outline_usable(workspace_id, outline_id)
    except FileNotFoundError as exc:
        try:
            workspace = repository.get_workspace(workspace_id)
        except FileNotFoundError:
            workspace = None
        return _render_outline(request, workspace=workspace, error=str(exc))
    return redirect("studio:outline")


@require_POST
def select_outline_view(request):
    repository = WorkspaceRepository()
    workspace_id = request.POST.get("workspace_id", "")
    outline_id = request.POST.get("outline_id", "")
    workspace = repository.get_workspace(workspace_id)

    if not repository.outline_exists(workspace_id, outline_id):
        return _render_outline(
            request,
            workspace=workspace,
            error="请选择有效的大纲。",
            genre=workspace.get("genre"),
        )

    workspace = repository.update_workspace(
        workspace_id, selected_outline_id=outline_id
    )
    return redirect("studio:project_workbench", project_id=workspace["project_id"])


def script_library_page(request):
    repository = WorkspaceRepository()
    try:
        data = repository.list_usable_outlines(CURRENT_WORKSPACE_ID)
    except FileNotFoundError:
        data = {
            "id": CURRENT_WORKSPACE_ID,
            "genre": "",
            "episode_count": EPISODE_COUNT,
            "episode_duration_minutes": EPISODE_DURATION_MINUTES,
            "episode_duration_label": EPISODE_DURATION_LABEL,
            "usable_outlines": [],
        }
    return _render_script_library(request, data)


def script_page(request, workspace_id):
    repository = WorkspaceRepository()
    workspace = repository.get_workspace(workspace_id)
    return _render_script(request, workspace)

def project_workbench_page(request, project_id):
    repository = WorkspaceRepository()
    try:
        workspace = repository.get_project_workspace(project_id)
    except FileNotFoundError as exc:
        raise Http404(str(exc)) from exc
    return _render_script(request, workspace)


@require_POST
def generate_script_view(request, workspace_id):
    repository = WorkspaceRepository()
    next_page = request.POST.get("next", "script")
    outline_id = request.POST.get("outline_id", "")

    try:
        outline = repository.start_script_generation(workspace_id, outline_id or None)
        _start_background_script_generation(workspace_id, outline["id"])
    except EXPECTED_GENERATION_ERRORS as exc:
        if next_page == "script_index":
            try:
                data = repository.list_usable_outlines(CURRENT_WORKSPACE_ID)
            except FileNotFoundError:
                data = {
                    "id": CURRENT_WORKSPACE_ID,
                    "genre": "",
                    "episode_count": EPISODE_COUNT,
                    "episode_duration_minutes": EPISODE_DURATION_MINUTES,
                    "episode_duration_label": EPISODE_DURATION_LABEL,
                    "usable_outlines": [],
                }
            return _render_script_library(request, data, error=str(exc))
        try:
            workspace = repository.get_workspace(workspace_id)
        except FileNotFoundError:
            workspace = None
        return _render_script(request, workspace, error=str(exc))

    if next_page == "script_index":
        return redirect("studio:script_index")
    if next_page == "project":
        return redirect("studio:project_workbench", project_id=outline["project_id"])
    return redirect("studio:script", workspace_id=workspace_id)


def episode_script_page(request, workspace_id, episode_number):
    repository = WorkspaceRepository()
    workspace = repository.get_episode_workspace(workspace_id, episode_number)
    selected_episode = workspace["selected_episode"]
    return _render_script(request, workspace, selected_episode=selected_episode)



def script_episode_context_page(request, workspace_id, script_id, episode_number):
    repository = WorkspaceRepository()
    workspace = repository.get_episode_workspace(workspace_id, episode_number, script_id=script_id)
    return _render_script(request, workspace, selected_episode=workspace["selected_episode"])


@require_POST
def generate_episode_script_view(request, workspace_id, episode_number):
    repository = WorkspaceRepository()
    script_id = int(request.POST.get("script_id") or 0) or None
    workspace = repository.get_workspace(workspace_id, script_id=script_id)
    try:
        task = repository.create_episode_script_task(
            workspace_id,
            episode_number,
            script_id=script_id,
        )
        if task["created"]:
            _start_background_episode_script_generation(task["id"])
    except EXPECTED_GENERATION_ERRORS as exc:
        selected_episode = next(
            (
                episode
                for episode in workspace.get("episodes", [])
                if episode.get("episode") == episode_number
            ),
            None,
        )
        return _render_script(
            request,
            workspace,
            error=str(exc),
            selected_episode=selected_episode,
        )
    if script_id:
        return redirect(
            "studio:script_episode_context",
            workspace_id=workspace_id,
            script_id=script_id,
            episode_number=episode_number,
        )
    return redirect(
        "studio:episode_script",
        workspace_id=workspace_id,
        episode_number=episode_number,
    )



@require_POST
def generate_characters_view(request, workspace_id):
    repository = WorkspaceRepository()
    script_id = int(request.POST.get("script_id") or 0) or None
    workspace = repository.get_workspace(workspace_id, script_id=script_id)
    try:
        task = repository.create_character_profile_task(
            workspace_id,
            request.POST.get("visual_style"),
            script_id=script_id,
        )
        if task["created"]:
            _start_background_character_profile_generation(task["id"])
    except EXPECTED_GENERATION_ERRORS as exc:
        return _render_script(
            request,
            workspace,
            error=str(exc),
            script_view="characters",
        )
    if script_id:
        return redirect(f'{reverse("studio:project_workbench", args=[workspace["project_id"]])}?view=characters')
    return redirect(f'{reverse("studio:script", args=[workspace_id])}?view=characters')


@require_POST
def generate_character_image_view(request, workspace_id, character_id):
    repository = WorkspaceRepository()
    script_id = int(request.POST.get("script_id") or 0) or None
    workspace = repository.get_workspace(workspace_id, script_id=script_id)
    try:
        task = repository.create_character_image_task(
            workspace_id,
            character_id,
            request.POST.get("image_prompt", ""),
        )
        if task["created"]:
            _start_background_character_image_generation(task["id"])
    except EXPECTED_GENERATION_ERRORS as exc:
        return _render_script(
            request,
            workspace,
            error=str(exc),
            script_view="characters",
        )
    if script_id:
        return redirect(f'{reverse("studio:project_workbench", args=[workspace["project_id"]])}?view=characters')
    return redirect(f'{reverse("studio:script", args=[workspace_id])}?view=characters')


@require_GET
def download_character_image_view(request, workspace_id, character_id):
    try:
        asset = WorkspaceRepository().get_latest_character_asset(
            workspace_id, character_id
        )
    except FileNotFoundError as exc:
        raise Http404(str(exc)) from exc
    return FileResponse(
        asset["file"],
        as_attachment=True,
        filename=asset["filename"],
    )


def storyboard_page(request, workspace_id):
    return storyboard_episode_page(request, workspace_id, 1)


def storyboard_episode_page(request, workspace_id, episode_number):
    repository = WorkspaceRepository()
    workspace = repository.get_episode_workspace(workspace_id, episode_number)
    return _render_storyboard(request, workspace)


def storyboard_script_episode_page(request, workspace_id, script_id, episode_number):
    repository = WorkspaceRepository()
    workspace = repository.get_episode_workspace(workspace_id, episode_number, script_id=script_id)
    return _render_storyboard(request, workspace)


@require_POST
def generate_storyboard_view(request, workspace_id, episode_number=1):
    repository = WorkspaceRepository()
    workspace = repository.get_episode_workspace(workspace_id, episode_number)

    try:
        task = repository.create_storyboard_task(workspace_id, episode_number)
        if task["created"]:
            _start_background_storyboard_generation(task["id"])
    except EXPECTED_GENERATION_ERRORS as exc:
        return _render_storyboard(request, workspace, error=str(exc))

    return redirect(
        "studio:storyboard_episode",
        workspace_id=workspace_id,
        episode_number=episode_number,
    )


@require_POST
def generate_storyboard_script_view(request, workspace_id, script_id, episode_number):
    repository = WorkspaceRepository()
    workspace = repository.get_episode_workspace(workspace_id, episode_number, script_id=script_id)
    try:
        task = repository.create_storyboard_task(
            workspace_id,
            episode_number,
            script_id=script_id,
        )
        if task["created"]:
            _start_background_storyboard_generation(task["id"])
    except EXPECTED_GENERATION_ERRORS as exc:
        return _render_storyboard(request, workspace, error=str(exc))
    return redirect(
        "studio:storyboard_script_episode",
        workspace_id=workspace_id,
        script_id=script_id,
        episode_number=episode_number,
    )


@require_GET
def workbench_context_view(request, workspace_id):
    stage = request.GET.get("stage", "storyboard")
    repository = WorkspaceRepository()
    if stage == "script":
        project_id = int(request.GET.get("project_id", "0"))
        repository.get_project_workspace(project_id)
        return redirect("studio:project_workbench", project_id=project_id)

    script_id = int(request.GET.get("script_id", "0"))
    episode_number = int(request.GET.get("episode_number", "1"))
    repository.get_episode_workspace(workspace_id, episode_number, script_id=script_id)
    route_name = "studio:video_script_episode" if stage == "video" else "studio:storyboard_script_episode"
    return redirect(route_name, workspace_id=workspace_id, script_id=script_id, episode_number=episode_number)


@require_GET
def task_status_view(request, task_id):
    repository = WorkspaceRepository()
    try:
        task = repository.get_task(task_id)
    except FileNotFoundError:
        return JsonResponse({"error": "任务不存在"}, status=404)

    if task["status"] == GenerationTask.STATUS_SUCCEEDED:
        script_id = None
        route_name = None
        if task["task_type"] == GenerationTask.TYPE_SCRIPT:
            project_id = task["input_snapshot"].get("project_id")
            task["result_url"] = request.build_absolute_uri(
                reverse("studio:project_workbench", args=[project_id])
            )
        elif task["task_type"] in {
            GenerationTask.TYPE_CHARACTER_PROFILE,
            GenerationTask.TYPE_CHARACTER_IMAGE,
        }:
            project_id = task["input_snapshot"].get("project_id")
            if project_id:
                task["result_url"] = request.build_absolute_uri(
                    f'{reverse("studio:project_workbench", args=[project_id])}'
                    "?view=characters"
                )
            else:
                task["result_url"] = request.build_absolute_uri(
                    f'{reverse("studio:script", args=[task["workspace_id"]])}'
                    "?view=characters"
                )
        elif task["task_type"] == GenerationTask.TYPE_COVER_IMAGE:
            project_id = task["input_snapshot"].get("project_id")
            if project_id:
                task["result_url"] = request.build_absolute_uri(
                    f'{reverse("studio:project_workbench", args=[project_id])}'
                    "?view=covers"
                )
            else:
                task["result_url"] = request.build_absolute_uri(
                    f'{reverse("studio:script", args=[task["workspace_id"]])}'
                    "?view=covers"
                )
        elif task["task_type"] == GenerationTask.TYPE_STORYBOARD:
            episode_number = int(
                task["input_snapshot"].get("episode_number")
                or task["target_id"]
                or 1
            )
            script_id = task["input_snapshot"].get("script_id")
            route_name = (
                "studio:storyboard_script_episode"
                if script_id
                else "studio:storyboard_episode"
            )
        elif task["task_type"] == GenerationTask.TYPE_EPISODE_SCRIPT:
            episode_number = int(
                task["input_snapshot"].get("episode")
                or task["target_id"]
                or 1
            )
            script_id = task["input_snapshot"].get("script_id")
            route_name = (
                "studio:script_episode_context"
                if script_id
                else "studio:episode_script"
            )
        if route_name:
            args = [task["workspace_id"], episode_number]
            if script_id:
                args = [task["workspace_id"], script_id, episode_number]
            task["result_url"] = request.build_absolute_uri(
                reverse(route_name, args=args)
            )
    return JsonResponse(task)


@require_POST
def retry_generation_task_view(request, task_id):
    from .generation_queue import retry_task

    try:
        task = GenerationTask.objects.select_related("project").get(pk=task_id)
    except GenerationTask.DoesNotExist as exc:
        raise Http404("\u751f\u6210\u4efb\u52a1\u4e0d\u5b58\u5728\u3002") from exc
    try:
        retry_task(task)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    if request.headers.get("Accept") == "application/json":
        return JsonResponse(WorkspaceRepository().get_task(task.id))
    return redirect(request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("studio:outline"))



def _run_character_profile_generation(task_id):
    close_old_connections()
    repository = WorkspaceRepository()
    try:
        task = repository.start_task(task_id)
        script_id = task["input_snapshot"].get("script_id")
        workspace = repository.get_workspace(task["workspace_id"], script_id=script_id)
        profiles = generate_character_profiles(
            llm_provider_for(ModelAssignment.PURPOSE_CHARACTER_PROFILE),
            workspace["selected_outline"],
            workspace.get("episodes", []),
            task["input_snapshot"].get("visual_style", workspace["character_visual_style"]),
        )
        repository.save_character_profiles(task["workspace_id"], profiles, script_id=script_id)
        repository.finish_task(task_id, result={"character_count": len(profiles)})
    except Exception as exc:
        logger.exception("Character profile generation task %s failed", task_id)
        try:
            repository.fail_task(task_id, exc)
        except Exception:
            logger.exception("Could not mark character profile task %s failed", task_id)
    finally:
        close_old_connections()



def _run_character_image_generation(task_id):
    close_old_connections()
    repository = WorkspaceRepository()
    try:
        task = repository.start_task(task_id)
        character_id = int(task["target_id"])
        character = repository.get_character(task["workspace_id"], character_id)
        effective_prompt = apply_character_visual_style(
            character["image_prompt"],
            task["input_snapshot"].get("visual_style", character["visual_style"]),
        )
        result = image_provider_for().generate_image(effective_prompt)
        asset = repository.save_character_asset(
            task["workspace_id"], character_id, result, prompt_snapshot=effective_prompt
        )
        repository.finish_task(
            task_id,
            result={"character_id": character_id, "image_url": asset["image_url"]},
        )
    except Exception as exc:
        logger.exception("Character image generation task %s failed", task_id)
        try:
            repository.fail_task(task_id, exc)
        except Exception:
            logger.exception("Could not mark character image task %s failed", task_id)
    finally:
        close_old_connections()




def _run_episode_script_generation(task_id):
    close_old_connections()
    repository = WorkspaceRepository()
    task = None
    try:
        task = repository.start_task(task_id)
        workspace_id = task["workspace_id"]
        script_id = task["input_snapshot"].get("script_id")
        episode_number = int(task["input_snapshot"].get("episode") or task["target_id"])
        workspace = repository.get_workspace(workspace_id, script_id=script_id)
        episodes = workspace.get("episodes", [])
        episode = next(
            item for item in episodes if item.get("episode") == episode_number
        )
        previous_episode = next(
            (
                item
                for item in episodes
                if item.get("episode") == episode_number - 1
            ),
            None,
        )
        next_episode = next(
            (
                item
                for item in episodes
                if item.get("episode") == episode_number + 1
            ),
            None,
        )
        generated = generate_episode_script(
            llm_provider_for(ModelAssignment.PURPOSE_EPISODE_SCRIPT),
            workspace["selected_outline"],
            episode,
            previous_episode=previous_episode,
            next_episode=next_episode,
        )
        if isinstance(generated, dict):
            full_script = generated["episode_script"]
            pacing_payload = generated.get("pacing")
        else:
            full_script = generated
            pacing_payload = None
        repository.save_episode_script(
            workspace_id,
            episode_number,
            full_script,
            script_id=script_id,
            pacing_payload=pacing_payload,
        )
        repository.finish_task(task_id, result={"episode_number": episode_number})
    except Exception as exc:
        logger.exception("Episode script generation task %s failed", task_id)
        if task is not None:
            try:
                repository.mark_episode_script_generation_failed(
                    task["workspace_id"],
                    int(task["input_snapshot"].get("episode") or task["target_id"]),
                    exc,
                    script_id=task["input_snapshot"].get("script_id"),
                )
            except Exception:
                logger.exception("Could not mark episode script task %s failed", task_id)
        try:
            repository.fail_task(task_id, exc)
        except Exception:
            logger.exception("Could not mark generation task %s failed", task_id)
    finally:
        close_old_connections()



def _run_storyboard_generation(task_id):
    close_old_connections()
    repository = WorkspaceRepository()
    try:
        task = repository.start_task(task_id)
        workspace_id = task["workspace_id"]
        episode_number = int(task["input_snapshot"].get("episode_number") or task["target_id"] or 1)
        script_id = task["input_snapshot"].get("script_id")
        episode = repository.get_episode(workspace_id, episode_number, script_id=script_id)
        storyboard_kwargs = {}
        if episode.get("pacing_payload"):
            storyboard_kwargs["pacing"] = episode["pacing_payload"]
        prompts = generate_storyboard(
            llm_provider_for(ModelAssignment.PURPOSE_STORYBOARD),
            episode["full_script"],
            episode_number=episode_number,
            **storyboard_kwargs,
        )
        repository.save_storyboard_for_episode(
            workspace_id,
            episode_number,
            prompts,
            script_id=script_id,
        )
        repository.finish_task(
            task_id,
            result={
                "episode_number": episode_number,
                "storyboard_prompt_count": len(prompts),
            },
        )
    except Exception as exc:
        logger.exception("Storyboard generation task %s failed", task_id)
        try:
            repository.fail_task(task_id, exc)
        except Exception:
            logger.exception("Could not mark storyboard task %s failed", task_id)
    finally:
        close_old_connections()



def _run_script_generation(workspace_id, outline_id):
    close_old_connections()
    repository = WorkspaceRepository()
    try:
        workspace = repository.get_workspace(workspace_id)
        outline = _outline_by_id(workspace, outline_id)
        if outline is None:
            raise FileNotFoundError(f"Outline not found: {outline_id}")
        payload = generate_script(llm_provider_for(ModelAssignment.PURPOSE_SCRIPT), outline)
        repository.save_script_for_outline(
            workspace_id,
            outline_id,
            payload["script_plan"],
            payload["episode_1_script"],
            episode_1_pacing=payload["episode_1_pacing"],
        )
    except EXPECTED_GENERATION_ERRORS as exc:
        logger.exception("Script generation failed for outline %s", outline_id)
        try:
            repository.mark_script_generation_failed(workspace_id, outline_id, exc)
        except FileNotFoundError:
            logger.exception("Could not mark failed script generation for outline %s", outline_id)
    finally:
        close_old_connections()


# Compatibility hooks retained for tests and callers; the persistent worker
# now owns execution after the request has committed the task.
def _start_background_character_profile_generation(task_id):
    return task_id


def _start_background_character_image_generation(task_id):
    return task_id


def _start_background_episode_script_generation(task_id):
    return task_id


def _start_background_storyboard_generation(task_id):
    return task_id


def _start_background_script_generation(workspace_id, outline_id):
    return workspace_id, outline_id


def _render_outline(request, workspace=None, error=None, genre=None):
    selected_genre = genre or (workspace or {}).get("genre")
    return render(
        request,
        "studio/outline.html",
        {
            "genres": GENRES,
            "episode_count": EPISODE_COUNT,
            "episode_duration_minutes": EPISODE_DURATION_MINUTES,
            "workspace": workspace,
            "episode_duration_label": EPISODE_DURATION_LABEL,
            "selected_genre": selected_genre,
            "error": error,
            "active_nav": "outline",
        },
    )


def _render_script_library(request, data, error=None):
    return render(
        request,
        "studio/script_library.html",
        {
            "workspace": data,
            "usable_outlines": data.get("usable_outlines", []),
            "error": error,
            "active_nav": "projects",
        },
    )


def _render_script(
    request, workspace, error=None, selected_episode=None, script_view=None
):
    requested_view = script_view or request.GET.get("view", "episodes")
    if selected_episode is not None:
        requested_view = "episodes"
    workspace = decorate_cover_workspace(workspace)
    _decorate_episode_workflows(workspace, selected_episode)
    active_view = requested_view if requested_view in {"episodes", "characters", "covers"} else "episodes"
    return render(
        request,
        "studio/script.html",
        {
            "workspace": workspace,
            "selected_outline": _selected_outline(workspace or {}),
            "selected_episode": selected_episode,
            "script_view": active_view,
            "error": error,
            "active_nav": "script",
        },
    )

def _decorate_episode_workflows(workspace, selected_episode=None):
    script_id = (workspace or {}).get("script_id")
    if not script_id:
        return
    latest_by_episode = {}
    runs = (
        EpisodeWorkflowRun.objects.filter(episode__script_id=script_id)
        .select_related("episode__script__project")
        .order_by("episode__episode_number", "-created_at", "-id")
    )
    for run in runs:
        latest_by_episode.setdefault(run.episode.episode_number, workflow_payload(run))
    for episode in workspace.get("episodes", []):
        episode["workflow"] = latest_by_episode.get(episode.get("episode"))
    if selected_episode is not None:
        selected_episode["workflow"] = latest_by_episode.get(
            selected_episode.get("episode")
        )


def _render_storyboard(request, workspace, error=None):
    return render(
        request,
        "studio/storyboard.html",
        {
            "workspace": workspace,
            "error": error,
            "active_nav": "storyboard",
        },
    )


def _selected_outline(workspace):
    if workspace.get("selected_outline"):
        return workspace["selected_outline"]

    selected_outline_id = workspace.get("selected_outline_id")
    return _outline_by_id(workspace, selected_outline_id)


def _outline_by_id(workspace, outline_id):
    outline_groups = [workspace.get("outlines"), workspace.get("usable_outlines")]
    for outlines in outline_groups:
        if not isinstance(outlines, list):
            continue
        for outline in outlines:
            if isinstance(outline, dict) and outline.get("id") == outline_id:
                return outline
    return None

@require_POST
def delete_character_view(request, workspace_id, character_id):
    script_id = int(request.POST.get("script_id") or 0) or None
    try:
        character = WorkspaceRepository().delete_character(
            workspace_id,
            character_id,
            script_id=script_id,
        )
    except (Character.DoesNotExist, FileNotFoundError) as exc:
        raise Http404(str(exc)) from exc
    if script_id:
        project_id = character.script.outline_id
        return redirect(f'{reverse("studio:project_workbench", args=[project_id])}?view=characters')
    return redirect(f'{reverse("studio:script", args=[workspace_id])}?view=characters')
