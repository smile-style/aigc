from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .constants import EPISODE_COUNT, EPISODE_DURATION_MINUTES, GENRES
from .llm.provider import (
    LLMAPIError,
    LLMConfigurationError,
    LLMJSONParseError,
    LLMProvider,
)
from .repositories.workspace import JsonWorkspaceRepository
from .services.outline import generate_outlines
from .services.script import generate_script
from .services.storyboard import generate_storyboard

EXPECTED_GENERATION_ERRORS = (
    FileNotFoundError,
    LLMAPIError,
    LLMConfigurationError,
    LLMJSONParseError,
    ValueError,
)


def outline_page(request):
    return _render_outline(request)


@require_POST
def generate_outlines_view(request):
    genre = request.POST.get("genre", "")
    repository = JsonWorkspaceRepository()
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
def select_outline_view(request):
    repository = JsonWorkspaceRepository()
    workspace_id = request.POST.get("workspace_id", "")
    outline_id = request.POST.get("outline_id", "")
    workspace = repository.get_workspace(workspace_id)
    outlines = workspace.get("outlines")

    if not isinstance(outlines, list):
        return _render_outline(
            request,
            workspace=workspace,
            error="请选择有效的大纲。",
            genre=workspace.get("genre"),
        )

    has_outline = any(
        isinstance(outline, dict) and outline.get("id") == outline_id for outline in outlines
    )
    if not has_outline:
        return _render_outline(
            request,
            workspace=workspace,
            error="请选择有效的大纲。",
            genre=workspace.get("genre"),
        )

    repository.update_workspace(workspace_id, selected_outline_id=outline_id)
    return redirect("studio:script", workspace_id=workspace_id)


def script_page(request, workspace_id):
    repository = JsonWorkspaceRepository()
    workspace = repository.get_workspace(workspace_id)
    return _render_script(request, workspace)


@require_POST
def generate_script_view(request, workspace_id):
    repository = JsonWorkspaceRepository()
    workspace = repository.get_workspace(workspace_id)

    try:
        selected_outline = _selected_outline(workspace)
        if selected_outline is None:
            raise ValueError("请先选择一个大纲，再生成剧本。")

        payload = generate_script(LLMProvider.from_env(), selected_outline)
        workspace = repository.update_workspace(
            workspace_id,
            script_plan=payload["script_plan"],
            episode_1_script=payload["episode_1_script"],
        )
    except EXPECTED_GENERATION_ERRORS as exc:
        return _render_script(request, workspace, error=str(exc))

    return _render_script(request, workspace)


def storyboard_page(request, workspace_id):
    repository = JsonWorkspaceRepository()
    workspace = repository.get_workspace(workspace_id)
    return _render_storyboard(request, workspace)


@require_POST
def generate_storyboard_view(request, workspace_id):
    repository = JsonWorkspaceRepository()
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
        },
    )


def _render_script(request, workspace, error=None):
    return render(
        request,
        "studio/script.html",
        {
            "workspace": workspace,
            "selected_outline": _selected_outline(workspace),
            "error": error,
        },
    )


def _render_storyboard(request, workspace, error=None):
    return render(
        request,
        "studio/storyboard.html",
        {
            "workspace": workspace,
            "error": error,
        },
    )


def _selected_outline(workspace):
    selected_outline_id = workspace.get("selected_outline_id")
    outlines = workspace.get("outlines")
    if not isinstance(outlines, list):
        return None

    for outline in outlines:
        if isinstance(outline, dict) and outline.get("id") == selected_outline_id:
            return outline
    return None
