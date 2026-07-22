import pytest
from django.urls import reverse

from studio.models import Episode, GenerationTask, Outline, Project, Script


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


def test_update_cover_title_rejects_title_outside_limit(client):
    project, script, episode = create_cover_workspace()

    response = client.post(
        reverse("studio:update_episode_cover", args=[project.workspace_id, episode.episode_number]),
        {"script_id": script.id, "title": "太短"},
    )

    assert response.status_code == 400
    assert "6 到 10" in response.content.decode("utf-8")
