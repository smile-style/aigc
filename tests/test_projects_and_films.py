import io
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from django.core.files.base import ContentFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from studio.models import (
    CoverTemplate,
    Episode,
    EpisodeCover,
    Outline,
    Project,
    Script,
    VideoComposition,
)
from studio.publishing_models import PublishingAccount, PublishingTask
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

        default_response = client.get(reverse("studio:finished_films"))
        response = client.get(
            reverse("studio:finished_films"), {"project": first_outline.id}
        )

    projects = {item["project_id"]: item for item in response.context["film_projects"]}
    first_episode_row = projects[first_outline.id]["episodes"][0]
    assert response.status_code == 200
    assert set(projects) == {first_outline.id}
    assert default_response.context["film_projects"][0]["project_id"] == second_outline.id
    assert first_episode_row["latest"].id == latest.id
    assert [item.id for item in first_episode_row["history"]] == [old.id]
    assert response.context["film_count"] == 2


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


def test_douyin_package_contains_video_cover_and_copy(client, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        workspace = make_workspace()
        _, script, episode = make_project(workspace, "first", "First")
        composition = VideoComposition.objects.create(
            episode=episode,
            version=1,
            status=VideoComposition.STATUS_READY,
            exported_at=timezone.now(),
        )
        composition.video.save("final.mp4", ContentFile(b"finished-film"))
        template = CoverTemplate.objects.create(
            script=script,
            prompt_snapshot="cover prompt",
            background="covers/template.jpg",
            model="test",
        )
        cover = EpisodeCover.objects.create(
            template=template,
            episode=episode,
            title="First cover",
            image="covers/episode.jpg",
        )
        cover.image.save("cover.png", ContentFile(b"cover-image"))

        response = client.get(
            reverse("studio:download_douyin_package", args=[composition.id])
        )
        payload = b"".join(response.streaming_content)

    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    assert "douyin-package.zip" in response["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert set(archive.namelist()) == {
            "EP001-video.mp4",
            "EP001-cover.png",
            "EP001-发布文案.txt",
        }
        assert archive.read("EP001-video.mp4") == b"finished-film"
        assert archive.read("EP001-cover.png") == b"cover-image"
        copy = archive.read("EP001-发布文案.txt").decode("utf-8")
    assert "标题：First episode" in copy
    assert "简介：\nSummary" in copy
    assert "标签：#漫剧 #AI动画 #短剧" in copy


def test_douyin_is_manual_package_only_in_web_ui(client):
    workspace = make_workspace()
    _, _, episode = make_project(workspace, "first", "First")
    composition = VideoComposition.objects.create(
        episode=episode,
        version=1,
        status=VideoComposition.STATUS_READY,
        video="films/first.mp4",
        exported_at=timezone.now(),
    )
    PublishingAccount.objects.create(
        platform=PublishingAccount.PLATFORM_DOUYIN,
        remote_account_id="douyin-hidden",
        display_name="Hidden Douyin",
        credential_ciphertext="encrypted",
    )

    films = client.get(reverse("studio:finished_films"))
    system = client.get(reverse("studio:system_settings"))
    html = films.content.decode()

    assert films.status_code == 200
    assert list(films.context["publishing_accounts"]) == []
    assert f'data-modal-target="douyin-package-{composition.id}"' in html
    assert reverse("studio:download_douyin_package", args=[composition.id]) in html
    assert 'data-copy-target="douyin-title-' in html
    assert "https://creator.douyin.com/" in html
    assert "20260729-history-delete" in html
    assert reverse(
        "studio:publishing_oauth_login",
        args=[PublishingAccount.PLATFORM_DOUYIN],
    ) not in system.content.decode()
    assert "Hidden Douyin" not in system.content.decode()


@pytest.mark.parametrize("environment", ["prod", "pre"])
def test_runtime_compose_uses_its_own_data_directory(environment):
    compose = Path(
        f"deploy/{environment}/docker-compose.yaml"
    ).read_text(encoding="utf-8")

    data_root = f"/data/aigc_data/{environment}"
    assert f"source: {data_root}/db.sqlite3" in compose
    assert f"source: {data_root}/workspace" in compose
    assert f"source: {data_root}/media" in compose
    assert 'DB_ENGINE: "${DB_ENGINE:-sqlite}"' in compose
    assert "  build:" not in compose


@pytest.mark.parametrize("environment", ["prod", "pre"])
def test_runtime_compose_provides_optional_mysql_service(environment):
    compose = Path(
        f"deploy/{environment}/docker-compose.yaml"
    ).read_text(encoding="utf-8")
    mysql_config = Path("deploy/mysql/config/mysql.cnf").read_text(encoding="utf-8")

    assert "  aigc-db:" in compose
    assert 'profiles: ["mysql"]' in compose
    assert "image: registry.cn-shanghai.aliyuncs.com/uwa/mysql:8.0" in compose
    assert f"source: /data/aigc_data/{environment}/mysql/data" in compose
    assert "source: ../mysql/config/mysql.cnf" in compose
    assert "3306:3306" not in compose
    assert "character-set-server=utf8mb4" in mysql_config


@pytest.mark.parametrize(
    "compose_path",
    [
        "deploy/prod/docker-compose.yaml",
        "deploy/pre/docker-compose.yaml",
    ],
)
def test_runtime_compose_uses_prebuilt_application_image(compose_path):
    compose = Path(compose_path).read_text(encoding="utf-8")

    assert 'image: "${AIGC_IMAGE:?Set AIGC_IMAGE in .env}"' in compose
    assert "  build:" not in compose


def test_build_compose_owns_application_image_build():
    compose = Path("deploy/build/docker-compose.yaml").read_text(encoding="utf-8")

    assert 'image: "${AIGC_IMAGE:-aigc-studio:latest}"' in compose
    assert "    build:" in compose
    assert "dockerfile: deploy/build/Dockerfile" in compose
    assert "dockerfile: deploy/build/Dockerfile.base" in compose


@pytest.mark.parametrize("environment", ["prod", "pre"])
def test_runtime_composes_run_the_same_workers(environment):
    compose = Path(
        f"deploy/{environment}/docker-compose.yaml"
    ).read_text(encoding="utf-8")

    assert "generation-worker:" in compose
    assert "video-worker:" in compose
    assert "publish-worker:" in compose


def test_runtime_environments_reference_the_same_image():
    def values(path):
        return dict(
            line.split("=", 1)
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        )

    prod = values("deploy/prod/.env.example")
    pre = values("deploy/pre/.env.example")

    assert prod["AIGC_IMAGE"] == pre["AIGC_IMAGE"]
    assert prod["AIGC_HTTP_PORT"] == "8080"
    assert pre["AIGC_HTTP_PORT"] == "8081"


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


def test_finished_films_support_status_search_project_and_sort_filters(client):
    workspace = make_workspace()
    first_outline, first_script, first_episode = make_project(
        workspace, "first", "First"
    )
    second_outline, _, second_episode = make_project(
        workspace, "second", "Second", 2
    )
    later_episode = Episode.objects.create(
        script=first_script,
        episode_number=2,
        title="First finale",
        summary="Summary",
        key_conflict="Conflict",
        cliffhanger="Cliffhanger",
        full_script="Script",
    )
    now = timezone.now()
    first_composition = VideoComposition.objects.create(
        episode=first_episode,
        version=1,
        status=VideoComposition.STATUS_READY,
        video="films/first.mp4",
        exported_at=now - timedelta(minutes=10),
    )
    VideoComposition.objects.create(
        episode=later_episode,
        version=1,
        status=VideoComposition.STATUS_READY,
        video="films/finale.mp4",
        exported_at=now,
    )
    VideoComposition.objects.create(
        episode=second_episode,
        version=1,
        status=VideoComposition.STATUS_READY,
        video="films/second.mp4",
        exported_at=now - timedelta(minutes=5),
    )
    account = PublishingAccount.objects.create(
        platform=PublishingAccount.PLATFORM_BILIBILI,
        remote_account_id="films-test",
        display_name="Films test",
        credential_ciphertext="encrypted",
    )
    PublishingTask.objects.create(
        composition=first_composition,
        account=account,
        platform=account.platform,
        status=PublishingTask.STATUS_PUBLISHED,
        content_hash="published-hash",
        dedup_key="published-dedup",
    )

    default_response = client.get(reverse("studio:finished_films"))
    response = client.get(
        reverse("studio:finished_films"), {"project": first_outline.id}
    )

    assert response.status_code == 200
    assert response.context["film_stats"] == {
        "ready": 2,
        "unpublished": 1,
        "publishing": 0,
        "published": 1,
        "attention": 0,
    }
    assert default_response.context["filters"]["project"] == str(second_outline.id)
    html = response.content.decode()
    assert "film-summary-grid" in html
    assert "film-play-button" in html
    assert 'id="film-player"' in html

    published = client.get(
        reverse("studio:finished_films"),
        {"project": first_outline.id, "status": "published"},
    )
    assert published.context["filtered_count"] == 1
    assert published.context["film_projects"][0]["project_id"] == first_outline.id

    searched = client.get(
        reverse("studio:finished_films"),
        {"project": second_outline.id, "q": "Second"},
    )
    assert searched.context["filtered_count"] == 1
    assert searched.context["film_projects"][0]["project_id"] == second_outline.id

    selected = client.get(
        reverse("studio:finished_films"), {"project": second_outline.id}
    )
    assert selected.context["filtered_count"] == 1

    sorted_response = client.get(
        reverse("studio:finished_films"),
        {"project": first_outline.id, "sort": "episode_desc"},
    )
    assert [
        item["number"]
        for item in sorted_response.context["film_projects"][0]["episodes"]
    ] == [2, 1]

def test_delete_finished_film_removes_history_record_and_file(
    client, tmp_path, django_capture_on_commit_callbacks
):
    with override_settings(MEDIA_ROOT=tmp_path):
        workspace = make_workspace()
        outline, _, episode = make_project(workspace, "delete-history", "Delete history")
        old = VideoComposition.objects.create(
            episode=episode,
            version=1,
            status=VideoComposition.STATUS_READY,
            exported_at=timezone.now() - timedelta(hours=1),
        )
        old.video.save("old.mp4", ContentFile(b"old"))
        old_path = Path(old.video.path)
        latest = VideoComposition.objects.create(
            episode=episode,
            version=2,
            status=VideoComposition.STATUS_READY,
            exported_at=timezone.now(),
        )
        latest.video.save("latest.mp4", ContentFile(b"latest"))

        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(
                reverse("studio:delete_finished_film", args=[old.id])
            )

    assert response.status_code == 302
    assert f"project={outline.id}" in response["Location"]
    assert not VideoComposition.objects.filter(pk=old.id).exists()
    assert not old_path.exists()
    assert VideoComposition.objects.filter(pk=latest.id).exists()


def test_delete_finished_film_blocks_latest_and_published_history(client, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        workspace = make_workspace()
        _, _, episode = make_project(workspace, "protected-history", "Protected history")
        old = VideoComposition.objects.create(
            episode=episode,
            version=1,
            status=VideoComposition.STATUS_READY,
            video="films/old.mp4",
            exported_at=timezone.now() - timedelta(hours=1),
        )
        latest = VideoComposition.objects.create(
            episode=episode,
            version=2,
            status=VideoComposition.STATUS_READY,
            video="films/latest.mp4",
            exported_at=timezone.now(),
        )
        account = PublishingAccount.objects.create(
            platform=PublishingAccount.PLATFORM_BILIBILI,
            remote_account_id="protected-history",
            display_name="Protected history",
            credential_ciphertext="encrypted",
        )
        PublishingTask.objects.create(
            composition=old,
            account=account,
            platform=account.platform,
            content_hash="protected-history",
            dedup_key="protected-history",
        )

        old_response = client.post(reverse("studio:delete_finished_film", args=[old.id]))
        latest_response = client.post(reverse("studio:delete_finished_film", args=[latest.id]))

    assert old_response.status_code == 302
    assert latest_response.status_code == 302
    assert VideoComposition.objects.filter(pk__in=[old.id, latest.id]).count() == 2

def test_finished_films_renders_empty_library(client):
    response = client.get(reverse("studio:finished_films"))

    assert response.status_code == 200
    assert response.context["film_projects"] == []
    assert response.context["project_options"] == []
    assert response.context["film_count"] == 0
