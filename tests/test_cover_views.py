from io import BytesIO
from zipfile import ZipFile

import pytest
from django.core.files.base import ContentFile
from django.test import override_settings
from django.urls import reverse

from studio.models import (
    CoverTemplate,
    Episode,
    EpisodeCover,
    GenerationTask,
    Outline,
    Project,
    Script,
)


pytestmark = pytest.mark.django_db


def create_cover_workspace():
    project = Project.objects.create(
        workspace_id="cover-view-workspace",
        name="Cover view project",
        genre="都市",
        episode_count=1,
        episode_duration_minutes=2,
    )
    outline = Outline.objects.create(
        project=project,
        outline_id="cover-view-outline",
        position=1,
        title="封面项目",
        core_premise="核心设定",
        protagonist="主角设定",
        hook="强钩子",
        arc_summary="主线梗概",
    )
    project.selected_outline = outline
    project.save(update_fields=["selected_outline"])
    script = Script.objects.create(project=project, outline=outline)
    episode = Episode.objects.create(
        script=script,
        episode_number=1,
        title="第一集",
        summary="第一集摘要",
        key_conflict="第一集冲突",
        cliffhanger="仓库一夜被抢空",
    )
    return project, script, episode


def test_generate_cover_starts_one_background_task(client, monkeypatch):
    project, script, _ = create_cover_workspace()
    started = []
    monkeypatch.setattr(
        "studio.cover_views._start_background_cover_generation",
        lambda task_id: started.append(task_id),
    )

    url = reverse("studio:generate_cover", args=[project.workspace_id])
    first = client.post(url, {"script_id": script.id, "prompt": "横版无字封面提示词"})
    second = client.post(url, {"script_id": script.id, "prompt": "横版无字封面提示词"})

    assert first.status_code == 302
    assert second.status_code == 302
    assert GenerationTask.objects.filter(task_type=GenerationTask.TYPE_COVER_IMAGE).count() == 1
    assert len(started) == 1


def test_update_cover_title_rejects_empty_title(client):
    project, script, episode = create_cover_workspace()

    response = client.post(
        reverse("studio:update_episode_cover", args=[project.workspace_id, episode.episode_number]),
        {"script_id": script.id, "title": ""},
    )

    assert response.status_code == 400
    assert "最多 10" in response.content.decode("utf-8")


def test_select_cover_template_version_switches_and_redirects(client, monkeypatch):
    project, script, _ = create_cover_workspace()
    template = CoverTemplate.objects.create(
        script=script,
        prompt_snapshot="cover prompt",
        model="cover-model",
        version=2,
    )
    selected = []
    monkeypatch.setattr(
        "studio.cover_views.switch_cover_template_version",
        lambda current, version: selected.append((current.id, version)),
    )

    response = client.post(
        reverse(
            "studio:select_cover_template_version",
            args=[project.workspace_id, 1],
        ),
        {"script_id": script.id},
    )

    assert response.status_code == 302
    assert "view=covers" in response.url
    assert selected == [(template.id, 1)]


def test_select_cover_template_version_rejects_script_from_another_workspace(client):
    project, _, _ = create_cover_workspace()
    other_project = Project.objects.create(
        workspace_id="other-cover-workspace",
        name="Other cover project",
        genre="都市",
        episode_count=1,
        episode_duration_minutes=2,
    )
    other_outline = Outline.objects.create(
        project=other_project,
        outline_id="other-cover-outline",
        position=1,
        title="其他项目",
        core_premise="核心设定",
        protagonist="主角设定",
        hook="强钩子",
        arc_summary="主线梗概",
    )
    other_script = Script.objects.create(project=other_project, outline=other_outline)

    response = client.post(
        reverse(
            "studio:select_cover_template_version",
            args=[project.workspace_id, 1],
        ),
        {"script_id": other_script.id},
    )

    assert response.status_code == 404


def test_download_all_episode_covers_returns_ordered_zip(client, tmp_path):
    project, script, first_episode = create_cover_workspace()
    second_episode = Episode.objects.create(
        script=script,
        episode_number=2,
        title="第二集",
        summary="第二集摘要",
        key_conflict="第二集冲突",
        cliffhanger="第二集悬念",
    )
    with override_settings(MEDIA_ROOT=tmp_path):
        template = CoverTemplate.objects.create(
            script=script,
            prompt_snapshot="cover prompt",
            model="cover-model",
        )
        for episode, content in (
            (second_episode, b"cover-two"),
            (first_episode, b"cover-one"),
        ):
            cover = EpisodeCover.objects.create(
                template=template,
                episode=episode,
                title=episode.title,
            )
            cover.image.save(
                f"episode-{episode.episode_number}.jpg",
                ContentFile(content),
            )

        response = client.get(
            reverse("studio:download_all_episode_covers", args=[project.workspace_id]),
            {"script_id": script.id},
        )
        archive = ZipFile(BytesIO(b"".join(response.streaming_content)))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    assert (
        f"script-{script.id:06d}-episode-covers.zip"
        in response["Content-Disposition"]
    )
    assert archive.namelist() == ["EP001-cover.jpg", "EP002-cover.jpg"]
    assert archive.read("EP001-cover.jpg") == b"cover-one"
    assert archive.read("EP002-cover.jpg") == b"cover-two"


def test_download_all_episode_covers_returns_404_without_covers(client):
    project, script, _ = create_cover_workspace()

    response = client.get(
        reverse("studio:download_all_episode_covers", args=[project.workspace_id]),
        {"script_id": script.id},
    )

    assert response.status_code == 404
