import logging
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from django.db import close_old_connections
from django.http import FileResponse, Http404, HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from studio.models import CoverTemplate, CoverTemplateVersion, EpisodeCover, Script
from studio.repositories.workspace import WorkspaceRepository
from studio.services.covers import (
    COVER_GENERATION_SIZE,
    create_cover_generation_task,
    episode_for_workspace,
    normalize_cover_title,
    render_episode_cover,
    save_cover_template,
    switch_cover_template_version,
)
from studio.services.model_config import image_provider_for

logger = logging.getLogger(__name__)



@require_POST
def generate_cover_view(request, workspace_id):
    try:
        script_id = int(request.POST.get("script_id") or 0) or None
        task, created = create_cover_generation_task(
            workspace_id,
            script_id,
            request.POST.get("prompt"),
        )
        if created:
            _start_background_cover_generation(task.id)
        return redirect(_cover_redirect_url(task.project, task.input_snapshot.get("project_id")))
    except (ValueError, FileNotFoundError) as exc:
        return HttpResponseBadRequest(str(exc))


@require_POST
def update_episode_cover_view(request, workspace_id, episode_number):
    try:
        title = normalize_cover_title(request.POST.get("title"))
        script_id = int(request.POST.get("script_id") or 0) or None
        episode = episode_for_workspace(workspace_id, script_id, episode_number)
        try:
            template = episode.script.cover_template
        except CoverTemplate.DoesNotExist as exc:
            raise ValueError("请先生成封面母版。") from exc
        render_episode_cover(
            template, episode, title, title_customized=True
        )
        return redirect(_cover_redirect_url(episode.script.project, episode.script.outline_id))
    except (ValueError, FileNotFoundError) as exc:
        return HttpResponseBadRequest(str(exc))


@require_POST
def select_cover_template_version_view(request, workspace_id, version):
    script_id = int(request.POST.get("script_id") or 0) or None
    script = Script.objects.filter(
        pk=script_id,
        project__workspace_id=workspace_id,
    ).select_related("project").first()
    if script is None:
        raise Http404("剧本不存在。")
    try:
        template = script.cover_template
        switch_cover_template_version(template, version)
    except CoverTemplate.DoesNotExist as exc:
        raise Http404("封面母版尚未生成。") from exc
    except CoverTemplateVersion.DoesNotExist as exc:
        raise Http404("封面母版版本不存在。") from exc
    return redirect(_cover_redirect_url(script.project, script.outline_id))


@require_GET
def download_episode_cover_view(request, workspace_id, episode_number):
    script_id = int(request.GET.get("script_id") or 0) or None
    try:
        episode = episode_for_workspace(workspace_id, script_id, episode_number)
        cover = episode.cover
    except (FileNotFoundError, EpisodeCover.DoesNotExist) as exc:
        raise Http404("单集封面尚未生成。") from exc
    cover.image.open("rb")
    return FileResponse(
        cover.image,
        as_attachment=True,
        filename=f"EP{episode_number:03d}-cover.jpg",
    )


@require_GET
def download_all_episode_covers_view(request, workspace_id):
    script_id = int(request.GET.get("script_id") or 0) or None
    script = Script.objects.filter(
        pk=script_id,
        project__workspace_id=workspace_id,
    ).first()
    if script is None:
        raise Http404("剧本不存在。")

    covers = list(
        EpisodeCover.objects.filter(episode__script=script)
        .exclude(image="")
        .select_related("episode")
        .order_by("episode__episode_number")
    )
    if not covers:
        raise Http404("当前剧本还没有可下载的封面。")

    archive = BytesIO()
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
        for cover in covers:
            cover.image.open("rb")
            try:
                bundle.writestr(
                    f"EP{cover.episode.episode_number:03d}-cover.jpg",
                    cover.image.read(),
                )
            finally:
                cover.image.close()
    archive.seek(0)
    return FileResponse(
        archive,
        as_attachment=True,
        filename=f"script-{script.id:06d}-episode-covers.zip",
        content_type="application/zip",
    )


def _run_cover_generation(task_id):
    close_old_connections()
    repository = WorkspaceRepository()
    try:
        task = repository.start_task(task_id)
        script = Script.objects.select_related("outline", "project").get(
            pk=task["input_snapshot"]["script_id"],
            project__workspace_id=task["workspace_id"],
        )
        prompt = task["input_snapshot"]["prompt"]
        result = image_provider_for().generate_image(prompt, size=COVER_GENERATION_SIZE)
        template = save_cover_template(script, result, prompt)
        repository.finish_task(
            task_id,
            result={
                "script_id": script.id,
                "cover_template_id": template.id,
                "cover_version": template.version,
            },
        )
    except Exception as exc:
        logger.exception("Cover generation task %s failed", task_id)
        try:
            repository.fail_task(task_id, exc)
        except Exception:
            logger.exception("Could not mark cover generation task %s failed", task_id)
    finally:
        close_old_connections()


def _start_background_cover_generation(task_id):
    return task_id


def _cover_redirect_url(project, project_id):
    if project_id:
        return f'{reverse("studio:project_workbench", args=[project_id])}?view=covers'
    return f'{reverse("studio:script", args=[project.workspace_id])}?view=covers'
