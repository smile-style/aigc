from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone

from studio.models import (
    Episode,
    Outline,
    Project,
    PublishingAccount,
    PublishingTask,
    Script,
    VideoComposition,
)
from studio.publishing.base import AccountProfile, SubmissionResult, UploadResult
from studio.publishing.errors import PublishingAuthError, PublishingRetryableError
from studio.publishing.service import (
    claim_next_task,
    create_publishing_task,
    encode_credentials,
    process_claimed_task,
    recover_expired_leases,
    retry_task,
)


pytestmark = pytest.mark.django_db

@pytest.fixture(autouse=True)
def publishing_media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path



def make_composition(tmp_path):
    project = Project.objects.create(
        workspace_id="publish-workspace",
        name="Publish project",
        genre="都市",
        episode_count=1,
        episode_duration_minutes=2,
    )
    outline = Outline.objects.create(
        project=project,
        outline_id="outline-publish",
        position=1,
        title="发布测试",
        core_premise="Premise",
        protagonist="Lead",
        hook="Hook",
        arc_summary="Arc",
    )
    project.selected_outline = outline
    project.save(update_fields=["selected_outline"])
    script = Script.objects.create(project=project, outline=outline)
    episode = Episode.objects.create(
        script=script,
        episode_number=1,
        title="第一集",
        summary="本集简介",
        key_conflict="Conflict",
        cliffhanger="Cliffhanger",
    )
    composition = VideoComposition.objects.create(
        episode=episode,
        version=1,
        status=VideoComposition.STATUS_READY,
        exported_at=timezone.now(),
    )
    composition.video.save("result.mp4", ContentFile(b"publishable-video"))
    return project, composition


def make_account():
    return PublishingAccount.objects.create(
        platform=PublishingAccount.PLATFORM_BILIBILI,
        remote_account_id="123456",
        display_name="测试账号",
        credential_ciphertext=encode_credentials(
            {"SESSDATA": "session", "bili_jct": "csrf", "DedeUserID": "123456"}
        ),
    )


def metadata():
    return {
        "title": "第一集",
        "description": "本集简介",
        "tid": 21,
        "tags": "漫剧,AI动画",
        "copyright": 1,
    }


def permissive_platform(upload_result=None, submit_result=None):
    checker = SimpleNamespace(
        check_account=lambda credentials: AccountProfile("123456", "测试账号"),
        check_submission=lambda composition, values: values,
    )

    def upload(composition, credentials, progress):
        progress(composition.video.size, composition.video.size)
        return upload_result or UploadResult("media-file", {"upload_id": "upload-1"})

    uploader = SimpleNamespace(
        upload_media=upload,
        submit=lambda result, values, credentials: submit_result
        or SubmissionResult("BV1TEST", "https://www.bilibili.com/video/BV1TEST", payload={"code": 0}),
    )
    return SimpleNamespace(checker=checker, uploader=uploader)


def test_create_task_hashes_composition_and_deduplicates(tmp_path, monkeypatch):
    _, composition = make_composition(tmp_path)
    account = make_account()
    monkeypatch.setattr("studio.publishing.service.get_platform", lambda name: permissive_platform())

    first, created = create_publishing_task(composition, account, metadata())
    duplicate, duplicate_created = create_publishing_task(composition, account, metadata())
    republished, republished_created = create_publishing_task(
        composition, account, metadata(), force_republish=True
    )

    composition.refresh_from_db()
    assert created is True
    assert duplicate_created is False
    assert duplicate.id == first.id
    assert republished_created is True
    assert republished.id != first.id
    assert len(composition.content_hash) == 64
    assert first.metadata_snapshot["tags"] == ["漫剧", "AI动画"]


def test_worker_claims_and_completes_upload(tmp_path, monkeypatch):
    _, composition = make_composition(tmp_path)
    account = make_account()
    monkeypatch.setattr("studio.publishing.service.get_platform", lambda name: permissive_platform())
    task, _ = create_publishing_task(composition, account, metadata())

    claimed = claim_next_task("worker-1")
    result = process_claimed_task(claimed.id, "worker-1")

    result.refresh_from_db()
    assert claimed.id == task.id
    assert result.status == PublishingTask.STATUS_SUBMITTED
    assert result.progress_percent == 100
    assert result.remote_video_id == "BV1TEST"
    assert result.remote_url.endswith("BV1TEST")
    assert result.lease_owner == ""
    assert result.attempts.count() == 1


def test_retryable_error_waits_before_retry(tmp_path, monkeypatch):
    _, composition = make_composition(tmp_path)
    account = make_account()
    platform = permissive_platform()
    platform.uploader.upload_media = lambda *args: (_ for _ in ()).throw(
        PublishingRetryableError("网络暂时不可用")
    )
    monkeypatch.setattr("studio.publishing.service.get_platform", lambda name: platform)
    task, _ = create_publishing_task(composition, account, metadata())

    claimed = claim_next_task("worker-1")
    process_claimed_task(claimed.id, "worker-1")

    task.refresh_from_db()
    assert task.status == PublishingTask.STATUS_RETRY_WAIT
    assert task.error_code == "temporary_platform_error"
    assert task.next_retry_at > timezone.now()
    assert task.attempts.get().retryable is True


def test_retryable_error_honors_provider_retry_delay(tmp_path, monkeypatch):
    _, composition = make_composition(tmp_path)
    account = make_account()
    platform = permissive_platform()
    platform.uploader.upload_media = lambda *args: (_ for _ in ()).throw(
        PublishingRetryableError(
            "Rate limited",
            code="platform_rate_limited",
            details={"retry_after_seconds": 600},
        )
    )
    monkeypatch.setattr("studio.publishing.service.get_platform", lambda name: platform)
    task, _ = create_publishing_task(composition, account, metadata())

    claimed = claim_next_task("worker-1")
    process_claimed_task(claimed.id, "worker-1")

    task.refresh_from_db()
    assert task.status == PublishingTask.STATUS_RETRY_WAIT
    assert task.next_retry_at >= timezone.now() + timedelta(seconds=590)
    assert task.error_details["retry_after_seconds"] == 600




def test_expired_submit_lease_becomes_outcome_unknown(tmp_path, monkeypatch):
    _, composition = make_composition(tmp_path)
    account = make_account()
    monkeypatch.setattr("studio.publishing.service.get_platform", lambda name: permissive_platform())
    task, _ = create_publishing_task(composition, account, metadata())
    task.status = task.STATUS_RUNNING
    task.stage = task.STAGE_SUBMITTING
    task.lease_owner = "lost-worker"
    task.lease_expires_at = timezone.now() - timedelta(seconds=1)
    task.save()

    recover_expired_leases()

    task.refresh_from_db()
    assert task.status == task.STATUS_OUTCOME_UNKNOWN
    assert task.error_code == "worker_lost_during_submit"
    with pytest.raises(Exception, match="不能重试"):
        retry_task(task)


def test_auth_failure_expires_account(tmp_path, monkeypatch):
    _, composition = make_composition(tmp_path)
    account = make_account()
    platform = permissive_platform()
    platform.checker.check_account = lambda credentials: (_ for _ in ()).throw(PublishingAuthError())
    monkeypatch.setattr("studio.publishing.service.get_platform", lambda name: platform)
    task, _ = create_publishing_task(composition, account, metadata())

    process_claimed_task(claim_next_task("worker-1").id, "worker-1")

    task.refresh_from_db()
    account.refresh_from_db()
    assert task.status == task.STATUS_FAILED
    assert account.status == account.STATUS_EXPIRED


def test_publishing_pages_and_status_api(client, tmp_path, monkeypatch):
    _, composition = make_composition(tmp_path)
    account = make_account()
    monkeypatch.setattr("studio.publishing.service.get_platform", lambda name: permissive_platform())
    task, _ = create_publishing_task(composition, account, metadata())

    films = client.get(reverse("studio:finished_films"))
    tasks = client.get(reverse("studio:publishing_tasks"))
    status = client.get(reverse("studio:publishing_task_status"), {"ids": str(task.id)})
    settings = client.get(reverse("studio:system_settings"))

    assert films.status_code == 200
    assert "发布到 Bilibili" in films.content.decode("utf-8")
    assert tasks.status_code == 200
    assert "平台投稿进度" in tasks.content.decode("utf-8")
    assert status.json()["tasks"][0]["status"] == "queued"
    assert "测试账号" in settings.content.decode("utf-8")


def test_create_task_view_returns_existing_duplicate(client, tmp_path, monkeypatch):
    _, composition = make_composition(tmp_path)
    account = make_account()
    monkeypatch.setattr("studio.publishing.service.get_platform", lambda name: permissive_platform())
    payload = {
        "composition_id": composition.id,
        "account_id": account.id,
        **metadata(),
    }

    first = client.post(
        reverse("studio:publishing_task_create"), payload, HTTP_ACCEPT="application/json"
    )
    second = client.post(
        reverse("studio:publishing_task_create"), payload, HTTP_ACCEPT="application/json"
    )

    assert first.status_code == 200
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert PublishingTask.objects.count() == 1
