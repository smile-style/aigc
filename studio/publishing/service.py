import hashlib
import json
import os
import secrets
from datetime import timedelta

from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from studio.models import (
    PublishingAccount,
    PublishingAttempt,
    PublishingLoginSession,
    PublishingTask,
    VideoComposition,
)

from .errors import (
    PublishingAuthError,
    PublishingCancelled,
    PublishingError,
    PublishingOutcomeUnknown,
    PublishingValidationError,
)
from .registry import get_platform
from .secret import decrypt_credentials, decrypt_login_key, encrypt_credentials, encrypt_login_key


LEASE_SECONDS = int(os.environ.get("PUBLISH_LEASE_SECONDS", "180"))
GLOBAL_CONCURRENCY = int(os.environ.get("PUBLISH_MAX_CONCURRENCY", "2"))
TASK_TIMEOUT_SECONDS = int(os.environ.get("PUBLISH_TASK_TIMEOUT_SECONDS", "7200"))
RETRY_DELAYS = (30, 120, 600)


def encode_credentials(credentials):
    return encrypt_credentials(credentials)


def account_credentials(account):
    try:
        value = decrypt_credentials(account.credential_ciphertext)
    except (ValueError, json.JSONDecodeError) as exc:
        raise PublishingAuthError("平台登录凭据无法解密，请重新登录。") from exc
    if not isinstance(value, dict):
        raise PublishingAuthError("平台登录凭据格式无效，请重新登录。")
    return {str(key): str(item) for key, item in value.items()}


def save_connected_account(platform, credentials, profile):
    defaults = {
        "display_name": profile.display_name,
        "credential_ciphertext": encode_credentials(credentials),
        "status": PublishingAccount.STATUS_CONNECTED,
        "profile_snapshot": profile.payload,
        "last_checked_at": timezone.now(),
        "last_error": "",
    }
    account, _ = PublishingAccount.objects.update_or_create(
        platform=platform,
        remote_account_id=profile.remote_account_id,
        defaults=defaults,
    )
    return account


def connect_cookie_account(raw_cookie, platform_name=PublishingAccount.PLATFORM_BILIBILI):
    if platform_name not in dict(PublishingAccount.PLATFORM_CHOICES):
        raise PublishingValidationError("不支持的发布平台。", details={"field": "platform"})
    platform = get_platform(platform_name)
    credentials = platform.login.parse_cookie_header(raw_cookie)
    if not credentials:
        platform_label = dict(PublishingAccount.PLATFORM_CHOICES)[platform_name]
        raise PublishingValidationError(
            f"请粘贴有效的 {platform_label} Cookie。", details={"field": "cookie"}
        )
    profile = platform.checker.check_account(credentials)
    return save_connected_account(platform_name, credentials, profile)


def start_login_session(platform_name=PublishingAccount.PLATFORM_BILIBILI):
    result = get_platform(platform_name).login.start()
    return PublishingLoginSession.objects.create(
        platform=platform_name,
        provider_key_ciphertext=encrypt_login_key(result.provider_key),
        login_url=result.login_url,
        expires_at=timezone.now() + timedelta(seconds=result.expires_in),
    )


def poll_login_session(session_id):
    session = PublishingLoginSession.objects.get(session_id=session_id)
    if session.status in {session.STATUS_SUCCEEDED, session.STATUS_EXPIRED, session.STATUS_FAILED}:
        return session
    if session.expires_at <= timezone.now():
        session.status = session.STATUS_EXPIRED
        session.error_message = "二维码已过期。"
        session.save(update_fields=["status", "error_message", "updated_at"])
        return session
    try:
        provider_key = decrypt_login_key(session.provider_key_ciphertext)
        result = get_platform(session.platform).login.poll(provider_key)
        if result.status == "succeeded":
            platform = get_platform(session.platform)
            profile = platform.checker.check_account(result.credentials)
            session.account = save_connected_account(session.platform, result.credentials, profile)
            session.status = session.STATUS_SUCCEEDED
        elif result.status == "expired":
            session.status = session.STATUS_EXPIRED
        elif result.status == "scanned":
            session.status = session.STATUS_SCANNED
        else:
            session.status = session.STATUS_PENDING
        session.error_message = result.message
    except PublishingError as exc:
        session.status = session.STATUS_FAILED
        session.error_message = str(exc)
    session.save(update_fields=["account", "status", "error_message", "updated_at"])
    return session


def check_publishing_account(account):
    try:
        profile = get_platform(account.platform).checker.check_account(account_credentials(account))
        account.display_name = profile.display_name
        account.remote_account_id = profile.remote_account_id
        account.profile_snapshot = profile.payload
        account.status = account.STATUS_CONNECTED
        account.last_error = ""
    except PublishingAuthError as exc:
        account.status = account.STATUS_EXPIRED
        account.last_error = str(exc)
        raise
    except PublishingError as exc:
        account.status = account.STATUS_ERROR
        account.last_error = str(exc)
        raise
    finally:
        account.last_checked_at = timezone.now()
        account.save()
    return account


def normalize_metadata(metadata):
    tags = metadata.get("tags") or []
    if isinstance(tags, str):
        tags = [item.strip() for item in tags.replace("，", ",").split(",") if item.strip()]
    return {
        "title": str(metadata.get("title") or "").strip(),
        "description": str(metadata.get("description") or "").strip(),
        "tid": metadata.get("tid"),
        "tags": list(dict.fromkeys(str(item).strip() for item in tags if str(item).strip())),
        "copyright": metadata.get("copyright") or 1,
        "source": str(metadata.get("source") or "").strip(),
        "cover": str(metadata.get("cover") or "").strip(),
        "dynamic": str(metadata.get("dynamic") or "").strip(),
    }


def ensure_composition_hash(composition):
    if composition.content_hash:
        return composition.content_hash
    if not composition.video:
        raise PublishingValidationError("成片文件不存在。", code="missing_video")
    digest = hashlib.sha256()
    composition.video.open("rb")
    try:
        for chunk in iter(lambda: composition.video.read(1024 * 1024), b""):
            digest.update(chunk)
    finally:
        composition.video.close()
    composition.content_hash = digest.hexdigest()
    composition.save(update_fields=["content_hash", "updated_at"])
    return composition.content_hash


def create_publishing_task(composition, account, metadata, *, force_republish=False):
    if composition.status != VideoComposition.STATUS_READY or not composition.video:
        raise PublishingValidationError("只有已导出的成片才能发布。")
    if account.status != PublishingAccount.STATUS_CONNECTED:
        raise PublishingAuthError()
    normalized = normalize_metadata(metadata)
    normalized = get_platform(account.platform).checker.check_submission(composition, normalized)
    content_hash = ensure_composition_hash(composition)
    metadata_hash = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    key_source = f"{account.platform}:{account.id}:{content_hash}:{metadata_hash}"
    if force_republish:
        key_source = f"{key_source}:{secrets.token_hex(12)}"
    dedup_key = hashlib.sha256(key_source.encode("utf-8")).hexdigest()
    try:
        with transaction.atomic():
            task, created = PublishingTask.objects.get_or_create(
                dedup_key=dedup_key,
                defaults={
                    "composition": composition,
                    "account": account,
                    "platform": account.platform,
                    "metadata_snapshot": normalized,
                    "content_hash": content_hash,
                },
            )
    except IntegrityError:
        task = PublishingTask.objects.get(dedup_key=dedup_key)
        created = False
    return task, created


def recover_expired_leases():
    now = timezone.now()
    expired = PublishingTask.objects.filter(status=PublishingTask.STATUS_RUNNING, lease_expires_at__lt=now)
    expired.filter(stage=PublishingTask.STAGE_SUBMITTING).update(
        status=PublishingTask.STATUS_OUTCOME_UNKNOWN,
        error_code="worker_lost_during_submit",
        error_message="提交稿件时 worker 中断，需要核对平台结果。",
        lease_owner="",
        lease_expires_at=None,
        updated_at=now,
    )
    expired.exclude(stage=PublishingTask.STAGE_SUBMITTING).update(
        status=PublishingTask.STATUS_RETRY_WAIT,
        next_retry_at=now,
        error_code="worker_lease_expired",
        error_message="任务执行中断，正在重新排队。",
        lease_owner="",
        lease_expires_at=None,
        updated_at=now,
    )


def claim_next_task(worker_id):
    now = timezone.now()
    with transaction.atomic():
        active = PublishingTask.objects.filter(
            status=PublishingTask.STATUS_RUNNING,
            lease_expires_at__gte=now,
        ).count()
        if active >= GLOBAL_CONCURRENCY:
            return None
        queryset = PublishingTask.objects.filter(
            Q(status=PublishingTask.STATUS_QUEUED)
            | Q(status=PublishingTask.STATUS_RETRY_WAIT, next_retry_at__lte=now)
        ).select_related("account").order_by("created_at", "id")
        if connection.features.has_select_for_update:
            queryset = queryset.select_for_update(skip_locked=connection.features.has_select_for_update_skip_locked)
        for task in queryset[:20]:
            account_active = PublishingTask.objects.filter(
                account=task.account,
                status=PublishingTask.STATUS_RUNNING,
                lease_expires_at__gte=now,
            ).count()
            if account_active >= task.account.max_concurrency:
                continue
            task.status = task.STATUS_RUNNING
            task.stage = task.STAGE_VALIDATING
            task.attempt_count += 1
            task.started_at = task.started_at or now
            task.heartbeat_at = now
            task.lease_owner = worker_id
            task.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            task.next_retry_at = None
            task.error_code = ""
            task.error_message = ""
            task.save()
            return task
    return None


def process_claimed_task(task_id, worker_id):
    task = PublishingTask.objects.select_related("account", "composition").get(pk=task_id)
    if task.status != task.STATUS_RUNNING or task.lease_owner != worker_id:
        return task
    attempt = PublishingAttempt.objects.create(
        task=task,
        attempt_number=task.attempt_count,
        stage=task.stage,
    )
    try:
        credentials = account_credentials(task.account)
        platform = get_platform(task.platform)
        platform.checker.check_account(credentials)
        metadata = platform.checker.check_submission(task.composition, task.metadata_snapshot)
        _set_stage(task, task.STAGE_PREPARING, progress=0)
        task.total_bytes = task.composition.video.size
        task.save(update_fields=["total_bytes", "updated_at"])
        _set_stage(task, task.STAGE_UPLOADING, progress=0)

        def progress(uploaded, total):
            current = PublishingTask.objects.only("cancel_requested").get(pk=task.pk)
            if current.cancel_requested:
                raise PublishingCancelled()
            percent = min(95, int((uploaded / max(1, total)) * 95))
            if task.started_at and timezone.now() >= task.started_at + timedelta(seconds=TASK_TIMEOUT_SECONDS):
                raise PublishingError("发布任务超过最大执行时间。", code="task_timeout")
            now = timezone.now()
            PublishingTask.objects.filter(pk=task.pk, lease_owner=worker_id).update(
                uploaded_bytes=uploaded,
                total_bytes=total,
                progress_percent=percent,
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                updated_at=now,
            )

        upload_result = platform.uploader.upload_media(task.composition, credentials, progress)
        task.upload_snapshot = upload_result.payload
        _set_stage(task, task.STAGE_SUBMITTING, progress=97)
        task.save(update_fields=["upload_snapshot", "updated_at"])
        result = platform.uploader.submit(upload_result, metadata, credentials)
        now = timezone.now()
        task.status = task.STATUS_SUBMITTED
        task.stage = task.STAGE_PROCESSING
        task.progress_percent = 100
        task.remote_video_id = result.remote_video_id
        task.remote_url = result.remote_url
        task.remote_status = result.status
        task.remote_payload = result.payload
        task.submitted_at = now
        task.finished_at = now
        _clear_lease(task)
        task.save()
        attempt.provider_response = result.payload
    except PublishingCancelled as exc:
        _finish_with_error(task, attempt, exc, task.STATUS_CANCELLED)
    except PublishingOutcomeUnknown as exc:
        _finish_with_error(task, attempt, exc, task.STATUS_OUTCOME_UNKNOWN)
    except PublishingAuthError as exc:
        task.account.status = task.account.STATUS_EXPIRED
        task.account.last_error = str(exc)
        task.account.last_checked_at = timezone.now()
        task.account.save(update_fields=["status", "last_error", "last_checked_at", "updated_at"])
        _finish_with_error(task, attempt, exc, task.STATUS_FAILED)
    except PublishingError as exc:
        should_retry = exc.retryable and task.attempt_count < task.max_attempts
        _finish_with_error(task, attempt, exc, task.STATUS_RETRY_WAIT if should_retry else task.STATUS_FAILED)
        if should_retry:
            delay = RETRY_DELAYS[min(task.attempt_count - 1, len(RETRY_DELAYS) - 1)]
            suggested_delay = exc.details.get("retry_after_seconds", 0)
            if isinstance(suggested_delay, (int, float)):
                delay = max(delay, min(int(suggested_delay), 86400))
            task.next_retry_at = timezone.now() + timedelta(seconds=delay + secrets.randbelow(max(1, delay // 5)))
            task.finished_at = None
            task.save(update_fields=["next_retry_at", "finished_at", "updated_at"])
    except Exception as exc:
        error = PublishingError(str(exc) or type(exc).__name__, code="unexpected_error")
        _finish_with_error(task, attempt, error, task.STATUS_FAILED)
    finally:
        attempt.stage = task.stage
        attempt.finished_at = timezone.now()
        attempt.save()
    return task


def retry_task(task):
    if task.status not in {task.STATUS_FAILED, task.STATUS_REJECTED}:
        raise PublishingValidationError("当前任务状态不能重试。")
    task.status = task.STATUS_QUEUED
    task.stage = task.STAGE_VALIDATING
    task.progress_percent = 0
    task.uploaded_bytes = 0
    task.max_attempts = task.attempt_count + 3
    task.next_retry_at = None
    task.cancel_requested = False
    task.error_code = ""
    task.error_message = ""
    task.finished_at = None
    task.save()
    return task


def republish_task(task):
    if task.status != task.STATUS_OUTCOME_UNKNOWN:
        raise PublishingValidationError("只有结果待确认的任务可以强制再次发布。")
    return create_publishing_task(
        task.composition, task.account, task.metadata_snapshot, force_republish=True
    )


def cancel_task(task):
    if task.status in {task.STATUS_QUEUED, task.STATUS_RETRY_WAIT}:
        task.status = task.STATUS_CANCELLED
        task.finished_at = timezone.now()
    elif task.status == task.STATUS_RUNNING:
        task.cancel_requested = True
    else:
        raise PublishingValidationError("当前任务状态不能取消。")
    task.save()
    return task


def reconcile_task(task):
    if task.status not in {task.STATUS_SUBMITTED, task.STATUS_OUTCOME_UNKNOWN}:
        raise PublishingValidationError("当前任务不需要核对平台结果。")
    if not task.remote_video_id:
        raise PublishingValidationError("任务没有可核对的平台稿件 ID。")
    task.stage = task.STAGE_RECONCILING
    task.save(update_fields=["stage", "updated_at"])
    result = get_platform(task.platform).uploader.query_submission(
        task.remote_video_id, account_credentials(task.account)
    )
    task.remote_status = result.status
    task.remote_payload = result.payload
    task.remote_url = result.remote_url or task.remote_url
    task.status = task.STATUS_PUBLISHED if result.status == "published" else task.STATUS_SUBMITTED
    task.error_code = ""
    task.error_message = ""
    task.save()
    return task


def serialize_task(task):
    return {
        "id": task.id,
        "platform": task.platform,
        "account": task.account.display_name,
        "status": task.status,
        "status_label": task.get_status_display(),
        "stage": task.stage,
        "stage_label": task.get_stage_display(),
        "progress": task.progress_percent,
        "uploaded_bytes": task.uploaded_bytes,
        "total_bytes": task.total_bytes,
        "attempt_count": task.attempt_count,
        "max_attempts": task.max_attempts,
        "next_retry_at": task.next_retry_at.isoformat() if task.next_retry_at else None,
        "error_code": task.error_code,
        "error": task.error_message,
        "remote_video_id": task.remote_video_id,
        "remote_url": task.remote_url,
        "updated_at": task.updated_at.isoformat(),
        "terminal": task.is_terminal or task.status in {task.STATUS_SUBMITTED, task.STATUS_OUTCOME_UNKNOWN},
    }


def _set_stage(task, stage, *, progress=None):
    task.stage = stage
    if progress is not None:
        task.progress_percent = progress
    now = timezone.now()
    task.heartbeat_at = now
    task.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    task.save()


def _clear_lease(task):
    task.lease_owner = ""
    task.lease_expires_at = None
    task.heartbeat_at = None


def _finish_with_error(task, attempt, error, status):
    now = timezone.now()
    task.status = status
    task.error_code = error.code
    task.error_message = str(error)
    task.error_details = error.details
    task.finished_at = now
    _clear_lease(task)
    task.save()
    attempt.error_code = error.code
    attempt.error_message = str(error)
    attempt.retryable = error.retryable
