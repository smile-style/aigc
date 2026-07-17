from datetime import timedelta
from pathlib import Path

import pytest
from django.core.files.base import ContentFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from studio.models import Episode, Outline, Project, Script, VideoComposition
from studio.repositories.workspace import WorkspaceRepository


pytestmark = pytest.mark.django_db


def make_project(workspace, outline_id, title, position=1):
    outline = Outline.objects.create(
        project=workspace,
        outline_id=outline_id,
        position=position,
        title=title,
        core_premise=f"{title} premise",
        protagonist=f"{title} protagonist",
        hook=f"{title} hook",
        arc_summary=f"{title} arc",
        is_usable=True,
        usable_at=timezone.now(),
    )
    script = Script.objects.create(project=workspace, outline=outline)
    episode = Episode.objects.create(
        script=script,
        episode_number=1,
        title=f"{title} episode",
        summary="Summary",
        key_conflict="Conflict",
        cliffhanger="Cliffhanger",
        full_script="Script",
    )
    return outline, script, episode


def make_workspace():
    return Project.objects.create(
        workspace_id="project-workspace",
        name="Project workspace",
        genre="Urban",
        episode_count=60,
        episode_duration_minutes=2,
    )


def test_project_workbench_does_not_change_global_selection(client):
    workspace = make_workspace()
    first_outline, first_script, _ = make_project(workspace, "first", "First", 1)
    second_outline, _, _ = make_project(workspace, "second", "Second", 2)
    workspace.selected_outline = second_outline
    workspace.save(update_fields=["selected_outline"])

    response = client.get(reverse("studio:project_workbench", args=[first_outline.id]))

    workspace.refresh_from_db()
    assert response.status_code == 200
    assert response.context["workspace"]["project_id"] == first_outline.id
    assert response.context["workspace"]["script_id"] == first_script.id
    assert response.context["selected_outline"]["title"] == "First"
    assert workspace.selected_outline_id == second_outline.id


def test_project_context_switch_redirects_to_requested_project(client):
    workspace = make_workspace()
    first_outline, _, _ = make_project(workspace, "first", "First")

    response = client.get(
        reverse("studio:workbench_context", args=[workspace.workspace_id]),
        {"stage": "script", "project_id": first_outline.id},
    )

    assert response.status_code == 302
    assert response["Location"] == reverse(
        "studio:project_workbench", args=[first_outline.id]
    )


def test_finished_films_are_grouped_by_project_and_latest_version(client, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        workspace = make_workspace()
        first_outline, _, first_episode = make_project(workspace, "first", "First")
        second_outline, _, second_episode = make_project(workspace, "second", "Second", 2)
        old = VideoComposition.objects.create(
            episode=first_episode,
            version=1,
            status=VideoComposition.STATUS_READY,
            exported_at=timezone.now() - timedelta(hours=1),
        )
        old.video.save("old.mp4", ContentFile(b"old"))
        latest = VideoComposition.objects.create(
            episode=first_episode,
            version=2,
            status=VideoComposition.STATUS_READY,
            exported_at=timezone.now(),
        )
        latest.video.save("latest.mp4", ContentFile(b"latest"))
        second = VideoComposition.objects.create(
            episode=second_episode,
            version=1,
            status=VideoComposition.STATUS_READY,
            exported_at=timezone.now() - timedelta(minutes=30),
        )
        second.video.save("second.mp4", ContentFile(b"second"))
        VideoComposition.objects.create(
            episode=second_episode,
            version=2,
            status=VideoComposition.STATUS_DRAFT,
        )

        response = client.get(reverse("studio:finished_films"))

    projects = {item["project_id"]: item for item in response.context["film_projects"]}
    first_episode_row = projects[first_outline.id]["episodes"][0]
    assert response.status_code == 200
    assert set(projects) == {first_outline.id, second_outline.id}
    assert first_episode_row["latest"].id == latest.id
    assert [item.id for item in first_episode_row["history"]] == [old.id]
    assert response.context["film_count"] == 3


def test_finished_film_download_targets_exact_version(client, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        workspace = make_workspace()
        _, _, episode = make_project(workspace, "first", "First")
        composition = VideoComposition.objects.create(
            episode=episode,
            version=1,
            status=VideoComposition.STATUS_READY,
            exported_at=timezone.now(),
        )
        composition.video.save("final.mp4", ContentFile(b"finished-film"))

        response = client.get(
            reverse("studio:download_finished_film", args=[composition.id])
        )

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"finished-film"
    assert "FINAL-v001.mp4" in response["Content-Disposition"]


def test_sqlite_compose_uses_server_data_directory():
    compose = Path("deploy/docker-compose.yaml").read_text(encoding="utf-8")

    assert "source: /data/aigc_data/db.sqlite3" in compose
    assert "source: /data/aigc_data/workspace" in compose
    assert "source: /data/aigc_data/media" in compose


def test_unscripted_project_can_open_without_using_global_selection(client):
    workspace = make_workspace()
    scripted_outline, _, _ = make_project(workspace, "scripted", "Scripted")
    unscripted_outline = Outline.objects.create(
        project=workspace,
        outline_id="unscripted",
        position=2,
        title="Unscripted",
        core_premise="Premise",
        protagonist="Protagonist",
        hook="Hook",
        arc_summary="Arc",
        is_usable=True,
    )
    workspace.selected_outline = scripted_outline
    workspace.save(update_fields=["selected_outline"])

    response = client.get(reverse("studio:project_workbench", args=[unscripted_outline.id]))

    assert response.status_code == 200
    assert response.context["workspace"]["project_id"] == unscripted_outline.id
    assert response.context["workspace"]["script_id"] is None
