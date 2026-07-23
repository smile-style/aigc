import logging
import os
import secrets
import socket
import uuid
from datetime import timedelta

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from studio.models import GenerationAttempt, GenerationTask


logger = logging.getLogger(__name__)

LEASE_SECONDS = int(os.environ.get("GENERATION_LEASE_SECONDS", "900"))
RETRY_DELAYS = (10, 30, 90)
WORKER_TASK_TYPES = {
    GenerationTask.TYPE_SCRIPT,
    GenerationTask.TYPE_EPISODE_SCRIPT,
    GenerationTask.TYPE_STORYBOARD,
    GenerationTask.TYPE_CHARACTER_PROFILE,
    GenerationTask.TYPE_CHARACTER_IMAGE,
    GenerationTask.TYPE_COVER_IMAGE,
}
NON_RETRYABLE_MARKERS = (
    "401",
    "403",
    "404",
    "api key",
    "authentication",
    "configuration",
    "missing api key",
    "invalid api key",
    "unsupported",
    "invalid parameter",
    "quota has been exhausted",
    "\u672a\u914d\u7f6e",
    "\u53c2\u6570\u9519\u8bef",
)
RETRYABLE_MARKERS = (
    "429",
    "502",
    "503",
    "504",
    "timeout",
    "timed out",
    "connection",
    "temporarily",
    "\u8d85\u65f6",
    "\u7a0d\u540e\u91cd\u8bd5",
    "winerror 10013",
)


def worker_id():
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def recover_expired_leases():
    now = timezone.now()
    expired_ids = list(
        GenerationTask.objects.filter(
            task_type__in=WORKER_TASK_TYPES,
            status=GenerationTask.STATUS_RUNNING,
            lease_expires_at__lt=now,
        ).values_list("id", flat=True)
    )
    for task_id in expired_ids:
        with transaction.atomic():
            task = GenerationTask.objects.select_for_update().get(pk=task_id)
            if task.status != task.STATUS_RUNNING or not task.lease_expires_at:
                continue
            if task.lease_expires_at >= now:
                continue
            task.lease_owner = ""
            task.lease_expires_at = None
            task.heartbeat_at = None
            task.error_code = "worker_lease_expired"
            task.error_message = "Worker lease expired; task returned to the queue."
            if task.attempt_count >= task.max_attempts:
                task.status = task.STATUS_FAILED
                task.finished_at = now
                task.next_retry_at = None
            else:
                task.status = task.STATUS_RETRY_WAIT
                task.next_retry_at = now
                task.finished_at = None
            task.save()


def claim_next_task(owner, task_types=None):
    now = timezone.now()
    allowed_types = set(task_types or WORKER_TASK_TYPES)
    with transaction.atomic():
        queryset = GenerationTask.objects.filter(
            task_type__in=allowed_types,
        ).filter(
            Q(status=GenerationTask.STATUS_PENDING)
            | Q(
                status=GenerationTask.STATUS_RETRY_WAIT,
                next_retry_at__lte=now,
            )
        ).order_by("-priority", "created_at", "id")
        if connection.features.has_select_for_update:
            queryset = queryset.select_for_update(
                skip_locked=connection.features.has_select_for_update_skip_locked
            )
        task = queryset.first()
        if task is None:
            return None
        return _claim_locked_task(task, owner, now)


def claim_task(task_id, owner):
    now = timezone.now()
    with transaction.atomic():
        task = GenerationTask.objects.select_for_update().get(pk=task_id)
        if task.status == task.STATUS_RUNNING and task.lease_owner == owner:
            return task
        eligible = task.status == task.STATUS_PENDING or (
            task.status == task.STATUS_RETRY_WAIT
            and (task.next_retry_at is None or task.next_retry_at <= now)
        )
        if not eligible:
            return None
        return _claim_locked_task(task, owner, now)


def _claim_locked_task(task, owner, now):
    task.status = task.STATUS_RUNNING
    task.attempt_count += 1
    task.started_at = task.started_at or now
    task.finished_at = None
    task.next_retry_at = None
    task.error_code = ""
    task.error_message = ""
    task.error_details = {}
    task.lease_owner = owner
    task.heartbeat_at = now
    task.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    task.save()
    GenerationAttempt.objects.create(
        task=task,
        attempt_number=task.attempt_count,
        worker_id=owner,
    )
    return task


def process_claimed_task(task_id, owner):
    task = GenerationTask.objects.select_related("project").get(pk=task_id)
    if task.status != task.STATUS_RUNNING or task.lease_owner != owner:
        return task
    attempt = task.attempts.get(attempt_number=task.attempt_count)
    try:
        result = _execute(task)
    except Exception as exc:
        logger.exception("Generation task %s failed", task.id)
        try:
            _mark_domain_failure(task, exc)
        except Exception:
            logger.exception("Could not update domain status for task %s", task.id)
        retryable, error_code = classify_error(exc)
        now = timezone.now()
        task.refresh_from_db()
        task.error_code = error_code
        task.error_message = str(exc)
        task.error_details = {"exception": type(exc).__name__}
        task.lease_owner = ""
        task.lease_expires_at = None
        task.heartbeat_at = None
        if retryable and task.attempt_count < task.max_attempts:
            delay = RETRY_DELAYS[min(task.attempt_count - 1, len(RETRY_DELAYS) - 1)]
            task.status = task.STATUS_RETRY_WAIT
            task.next_retry_at = now + timedelta(
                seconds=delay + secrets.randbelow(max(1, delay // 5))
            )
            task.finished_at = None
        else:
            task.status = task.STATUS_FAILED
            task.next_retry_at = None
            task.finished_at = now
        task.save()
        attempt.finished_at = now
        attempt.retryable = retryable
        attempt.error_code = error_code
        attempt.error_message = str(exc)
        attempt.save()
        return task

    now = timezone.now()
    task.refresh_from_db()
    task.status = task.STATUS_SUCCEEDED
    task.result_snapshot = result or {}
    task.error_code = ""
    task.error_message = ""
    task.error_details = {}
    task.progress_percent = 100
    task.lease_owner = ""
    task.lease_expires_at = None
    task.heartbeat_at = None
    task.next_retry_at = None
    task.finished_at = now
    task.save()
    attempt.finished_at = now
    attempt.succeeded = True
    attempt.save()
    return task


def run_task_now(task_id):
    owner = f"inline:{worker_id()}"
    task = claim_task(task_id, owner)
    if task is None:
        return GenerationTask.objects.get(pk=task_id)
    return process_claimed_task(task.id, owner)


def retry_task(task):
    if task.status not in {
        task.STATUS_FAILED,
        task.STATUS_CANCELLED,
    }:
        raise ValueError("Only failed or cancelled tasks can be retried.")
    task.status = task.STATUS_PENDING
    task.max_attempts = max(task.max_attempts, task.attempt_count + 3)
    task.next_retry_at = None
    task.finished_at = None
    task.error_code = ""
    task.error_message = ""
    task.error_details = {}
    task.lease_owner = ""
    task.lease_expires_at = None
    task.heartbeat_at = None
    task.save()
    return task


def update_progress(task_id, current, total):
    total = max(0, int(total))
    current = max(0, min(int(current), total)) if total else max(0, int(current))
    percent = int(current * 100 / total) if total else 0
    now = timezone.now()
    GenerationTask.objects.filter(pk=task_id).update(
        progress_current=current,
        progress_total=total,
        progress_percent=percent,
        heartbeat_at=now,
        lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
    )


def classify_error(error):
    message = str(error).lower()
    if any(marker in message for marker in NON_RETRYABLE_MARKERS):
        return False, "non_retryable_provider_error"
    if isinstance(error, (ValueError, FileNotFoundError)):
        return False, "invalid_generation_input"
    if any(marker in message for marker in RETRYABLE_MARKERS):
        return True, "temporary_provider_error"
    return False, "generation_failed"


def _execute(task):
    from studio.generation_handlers import HANDLERS

    try:
        handler = HANDLERS[task.task_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported generation task type: {task.task_type}") from exc
    return handler(task)


def _mark_domain_failure(task, error):
    if task.task_type == GenerationTask.TYPE_SCRIPT:
        from studio.repositories.workspace import WorkspaceRepository

        WorkspaceRepository().mark_script_generation_failed(
            task.project.workspace_id,
            task.target_id,
            error,
        )
    elif task.task_type == GenerationTask.TYPE_EPISODE_SCRIPT:
        from studio.repositories.workspace import WorkspaceRepository

        snapshot = task.input_snapshot or {}
        WorkspaceRepository().mark_episode_script_generation_failed(
            task.project.workspace_id,
            int(snapshot.get("episode") or task.target_id),
            error,
            script_id=snapshot.get("script_id"),
        )
