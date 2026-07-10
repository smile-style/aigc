import logging
import threading

from django.db import close_old_connections
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .constants import EPISODE_COUNT, EPISODE_DURATION_MINUTES, GENRES
from .llm.provider import (
    LLMAPIError,
    LLMConfigurationError,
    LLMJSONParseError,
    LLMProvider,
)
from .repositories.workspace import CURRENT_WORKSPACE_ID, WorkspaceRepository
from .services.outline import generate_outlines
from .services.script import generate_script
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
        outlines = generate_outlines(LLMProvider.from_env(), genre)
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

    repository.update_workspace(workspace_id, selected_outline_id=outline_id)
    return redirect("studio:script", workspace_id=workspace_id)


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
            "usable_outlines": [],
        }
    return _render_script_library(request, data)


def script_page(request, workspace_id):
    repository = WorkspaceRepository()
    workspace = repository.get_workspace(workspace_id)
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
    return redirect("studio:script", workspace_id=workspace_id)


def storyboard_page(request, workspace_id):
    repository = WorkspaceRepository()
    workspace = repository.get_workspace(workspace_id)
    return _render_storyboard(request, workspace)


@require_POST
def generate_storyboard_view(request, workspace_id):
    repository = WorkspaceRepository()
    workspace = repository.get_workspace(workspace_id)

    try:
        episode_1_script = workspace.get("episode_1_script", "")
        if not isinstance(episode_1_script, str) or not episode_1_script.strip():
            raise ValueError("请先生成第一集剧本，再生成分镜。")

        prompts = generate_storyboard(LLMProvider.from_env(), episode_1_script)
        workspace = repository.update_workspace(
            workspace_id,
            storyboard_prompts=prompts,
        )
    except EXPECTED_GENERATION_ERRORS as exc:
        return _render_storyboard(request, workspace, error=str(exc))

    return _render_storyboard(request, workspace)


def _start_background_script_generation(workspace_id, outline_id):
    worker = threading.Thread(
        target=_run_script_generation,
        args=(workspace_id, outline_id),
        daemon=True,
    )
    worker.start()
    return worker


def _run_script_generation(workspace_id, outline_id):
    close_old_connections()
    repository = WorkspaceRepository()
    try:
        workspace = repository.get_workspace(workspace_id)
        outline = _outline_by_id(workspace, outline_id)
        if outline is None:
            raise FileNotFoundError(f"Outline not found: {outline_id}")
        payload = generate_script(LLMProvider.from_env(), outline)
        repository.save_script_for_outline(
            workspace_id,
            outline_id,
            payload["script_plan"],
            payload["episode_1_script"],
        )
    except EXPECTED_GENERATION_ERRORS as exc:
        logger.exception("Script generation failed for outline %s", outline_id)
        try:
            repository.mark_script_generation_failed(workspace_id, outline_id, exc)
        except FileNotFoundError:
            logger.exception("Could not mark failed script generation for outline %s", outline_id)
    finally:
        close_old_connections()


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
            "active_nav": "script",
        },
    )


def _render_script(request, workspace, error=None):
    return render(
        request,
        "studio/script.html",
        {
            "workspace": workspace,
            "selected_outline": _selected_outline(workspace or {}),
            "error": error,
            "active_nav": "script",
        },
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