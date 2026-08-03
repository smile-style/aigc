import pytest
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


pytestmark = pytest.mark.django_db


@pytest.fixture
def publishing_records():
    project = Project.objects.create(
        workspace_id="publishing-partitions",
        name="Publishing partitions",
        genre="test",
        episode_count=1,
        episode_duration_minutes=2,
    )
    outline = Outline.objects.create(
        project=project,
        outline_id="publishing-partitions-outline",
        position=1,
        title="Partition project",
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
        title="Episode one",
        summary="Summary",
        key_conflict="Conflict",
        cliffhanger="Cliffhanger",
    )
    composition = VideoComposition.objects.create(
        episode=episode,
        version=1,
        status=VideoComposition.STATUS_READY,
        exported_at=timezone.now(),
    )
    account = PublishingAccount.objects.create(
        platform=PublishingAccount.PLATFORM_BILIBILI,
        remote_account_id="partition-account",
        display_name="Partition account",
        credential_ciphertext="encrypted",
    )

    def create_task(status, suffix, acknowledged=False):
        return PublishingTask.objects.create(
            composition=composition,
            account=account,
            platform=account.platform,
            status=status,
            metadata_snapshot={},
            content_hash=f"hash-{suffix}",
            dedup_key=f"dedup-{suffix}",
            finished_at=timezone.now() if status not in {
                PublishingTask.STATUS_QUEUED,
                PublishingTask.STATUS_RUNNING,
                PublishingTask.STATUS_RETRY_WAIT,
                PublishingTask.STATUS_SUBMITTED,
            } else None,
            acknowledged_at=timezone.now() if acknowledged else None,
        )

    return create_task


def test_active_view_keeps_unacknowledged_failures_out_of_history(client, publishing_records):
    queued = publishing_records(PublishingTask.STATUS_QUEUED, "queued")
    failed = publishing_records(PublishingTask.STATUS_FAILED, "failed")
    published = publishing_records(PublishingTask.STATUS_PUBLISHED, "published")

    active = client.get(reverse("studio:publishing_tasks"))
    history = client.get(reverse("studio:publishing_tasks"), {"view": "history"})

    assert [task.id for task in active.context["active_tasks"]] == [queued.id]
    assert [task.id for task in active.context["attention_tasks"]] == [failed.id]
    assert failed.id not in [task.id for task in history.context["publishing_tasks"]]
    assert published.id in [task.id for task in history.context["publishing_tasks"]]


def test_acknowledging_failed_task_moves_it_to_history(client, publishing_records):
    failed = publishing_records(PublishingTask.STATUS_FAILED, "acknowledge")

    response = client.post(
        reverse("studio:publishing_task_acknowledge", args=[failed.id]),
        {"note": "Reviewed"},
    )

    assert response.status_code == 302
    failed.refresh_from_db()
    assert failed.acknowledged_at is not None
    assert failed.acknowledgement_note == "Reviewed"

    active = client.get(reverse("studio:publishing_tasks"))
    history = client.get(reverse("studio:publishing_tasks"), {"view": "history"})
    assert failed.id not in [task.id for task in active.context["publishing_tasks"]]
    assert failed.id in [task.id for task in history.context["publishing_tasks"]]


def test_history_view_is_paginated(client, publishing_records):
    for index in range(31):
        publishing_records(
            PublishingTask.STATUS_PUBLISHED,
            f"history-{index}",
        )

    response = client.get(reverse("studio:publishing_tasks"), {"view": "history"})

    assert response.status_code == 200
    assert len(response.context["publishing_tasks"]) == 25
    assert response.context["page_obj"].paginator.num_pages == 2
