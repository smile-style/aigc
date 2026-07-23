from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from studio.constants import GENRES
from studio.generation_handlers import HANDLERS
from studio.generation_queue import (
    claim_next_task,
    process_claimed_task,
    recover_expired_leases,
    retry_task,
)
from studio.models import GenerationAttempt, GenerationTask, Outline, Project
from studio.repositories.workspace import WorkspaceRepository


pytestmark = pytest.mark.django_db


def make_task(**overrides):
    project = Project.objects.create(
        workspace_id=f"workspace-{Project.objects.count() + 1}",
        name="Reliable generation",
        genre="drama",
        episode_count=60,
        episode_duration_minutes=2,
    )
    values = {
        "project": project,
        "task_type": GenerationTask.TYPE_SCRIPT,
        "target_id": "outline-1",
        "input_snapshot": {},
    }
    values.update(overrides)
    return GenerationTask.objects.create(**values)


def test_claim_records_attempt_and_lease():
    task = make_task()

    claimed = claim_next_task("worker-1")

    assert claimed.id == task.id
    assert claimed.status == GenerationTask.STATUS_RUNNING
    assert claimed.attempt_count == 1
    assert claimed.lease_owner == "worker-1"
    assert claimed.lease_expires_at > timezone.now()
    assert GenerationAttempt.objects.get(task=task).attempt_number == 1


def test_process_claimed_task_persists_result(monkeypatch):
    task = make_task()
    monkeypatch.setitem(
        HANDLERS,
        GenerationTask.TYPE_SCRIPT,
        lambda current: {"task_id": current.id, "done": True},
    )

    claimed = claim_next_task("worker-1")
    result = process_claimed_task(claimed.id, "worker-1")

    result.refresh_from_db()
    attempt = result.attempts.get()
    assert result.status == GenerationTask.STATUS_SUCCEEDED
    assert result.progress_percent == 100
    assert result.result_snapshot == {"task_id": task.id, "done": True}
    assert result.lease_owner == ""
    assert attempt.succeeded is True


def test_temporary_failure_waits_for_automatic_retry(monkeypatch):
    task = make_task()
    monkeypatch.setitem(
        HANDLERS,
        GenerationTask.TYPE_SCRIPT,
        lambda current: (_ for _ in ()).throw(RuntimeError("HTTP 502 temporarily unavailable")),
    )

    claimed = claim_next_task("worker-1")
    result = process_claimed_task(claimed.id, "worker-1")

    result.refresh_from_db()
    attempt = result.attempts.get()
    assert result.status == GenerationTask.STATUS_RETRY_WAIT
    assert result.next_retry_at > timezone.now()
    assert result.error_code == "temporary_provider_error"
    assert attempt.retryable is True


def test_expired_lease_is_requeued():
    task = make_task(
        status=GenerationTask.STATUS_RUNNING,
        attempt_count=1,
        lease_owner="lost-worker",
        lease_expires_at=timezone.now() - timedelta(seconds=1),
    )

    recover_expired_leases()

    task.refresh_from_db()
    assert task.status == GenerationTask.STATUS_RETRY_WAIT
    assert task.next_retry_at <= timezone.now()
    assert task.error_code == "worker_lease_expired"
    assert task.lease_owner == ""


def test_failed_task_can_be_retried_manually():
    task = make_task(
        status=GenerationTask.STATUS_FAILED,
        attempt_count=3,
        max_attempts=3,
        error_code="generation_failed",
        error_message="failed",
        finished_at=timezone.now(),
    )

    retry_task(task)

    task.refresh_from_db()
    assert task.status == GenerationTask.STATUS_PENDING
    assert task.max_attempts == 6
    assert task.error_message == ""
    assert task.finished_at is None


def test_script_handler_executes_persistent_task_end_to_end(monkeypatch):
    repository = WorkspaceRepository()
    workspace = repository.create_workspace(GENRES[0], workspace_id="script-handler")
    repository.update_workspace(
        workspace["id"],
        outlines=[
            {
                "id": "outline-1",
                "title": "Persistent story",
                "core_premise": "A task survives the request process.",
                "protagonist": "Lin",
                "hook": "The worker resumes.",
                "arc_summary": "The production completes.",
            }
        ],
    )
    repository.update_workspace(workspace["id"], selected_outline_id="outline-1")
    started = repository.start_script_generation(workspace["id"], "outline-1")
    payload = {
        "script_plan": [
            {
                "episode": 1,
                "title": "Episode 1",
                "summary": "The task is claimed.",
                "key_conflict": "The request has ended.",
                "cliffhanger": "The worker continues.",
            }
        ],
        "episode_1_script": "A complete first episode.",
        "episode_1_pacing": {"duration_seconds": 90},
    }
    monkeypatch.setattr(
        "studio.generation_handlers.llm_provider_for",
        lambda purpose: object(),
    )
    monkeypatch.setattr(
        "studio.generation_handlers.generate_script",
        lambda provider, outline: payload,
    )

    claimed = claim_next_task("script-worker")
    result = process_claimed_task(claimed.id, "script-worker")

    saved = repository.get_workspace(workspace["id"])
    assert claimed.id == started["generation_task_id"]
    assert result.status == GenerationTask.STATUS_SUCCEEDED
    assert saved["episode_1_script"] == "A complete first episode."
    assert saved["episodes"][0]["pacing_payload"] == {"duration_seconds": 90}
    assert (
        Outline.objects.get(outline_id="outline-1").script_status
        == Outline.SCRIPT_READY
    )


def test_process_generation_tasks_once_drains_available_task(monkeypatch):
    task = make_task(task_type=GenerationTask.TYPE_CHARACTER_PROFILE)
    monkeypatch.setitem(
        HANDLERS,
        GenerationTask.TYPE_CHARACTER_PROFILE,
        lambda current: {"task_id": current.id},
    )
    output = StringIO()

    call_command(
        "process_generation_tasks",
        "--once",
        "--concurrency=2",
        stdout=output,
    )

    task.refresh_from_db()
    assert task.status == GenerationTask.STATUS_SUCCEEDED
    assert task.result_snapshot == {"task_id": task.id}
    assert "Processed 1 generation task(s)." in output.getvalue()
