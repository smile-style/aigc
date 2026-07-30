from django.db.models import F
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from studio.models import Episode, EpisodeWorkflowRun
from studio.services.episode_workflow import (
    advance_episode_workflow,
    create_episode_workflow,
    retry_episode_workflow,
    workflow_payload,
)


@require_POST
def start_episode_workflow_view(request, workspace_id, episode_number):
    episode = _episode(
        workspace_id,
        episode_number,
        request.POST.get("script_id"),
    )
    run, _ = create_episode_workflow(
        episode,
        generate_captioned_video="generate_captioned_video" in request.POST,
    )
    return redirect(
        _episode_script_url(episode)
        + "?workflow="
        + str(run.id)
    )


@require_GET
def episode_workflow_status_view(request, run_id):
    run = get_object_or_404(EpisodeWorkflowRun, pk=run_id)
    if run.status in {
        EpisodeWorkflowRun.STATUS_QUEUED,
        EpisodeWorkflowRun.STATUS_RUNNING,
    }:
        try:
            run = advance_episode_workflow(run.id)
        except Exception:
            run.refresh_from_db()
    payload = workflow_payload(run)
    payload["video_url"] = request.build_absolute_uri(
        reverse(
            "studio:video_script_episode",
            args=[
                payload["workspace_id"],
                payload["script_id"],
                payload["episode_number"],
            ],
        )
    )
    return JsonResponse(payload)


@require_POST
def retry_episode_workflow_view(request, run_id):
    run = get_object_or_404(EpisodeWorkflowRun, pk=run_id)
    try:
        run = retry_episode_workflow(run)
    except ValueError as exc:
        raise Http404(str(exc)) from exc
    return redirect(_episode_script_url(run.episode) + "?workflow=" + str(run.id))


def _episode(workspace_id, episode_number, script_id):
    filters = {
        "script__project__workspace_id": workspace_id,
        "episode_number": episode_number,
    }
    if script_id:
        filters["script_id"] = int(script_id)
    else:
        filters["script__project__selected_outline_id"] = F("script__outline_id")
    return get_object_or_404(
        Episode.objects.select_related("script__project"),
        **filters,
    )


def _episode_script_url(episode):
    return reverse(
        "studio:script_episode_context",
        args=[
            episode.script.project.workspace_id,
            episode.script_id,
            episode.episode_number,
        ],
    )
