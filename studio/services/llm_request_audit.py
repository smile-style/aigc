import hashlib
import json
import re
from contextlib import contextmanager
from threading import Lock

from django.db.models import Max
from django.utils import timezone

from studio.llm.provider import observe_llm_requests
from studio.models import GenerationTask, LLMRequestRecord, Project


AUDITED_PAYLOAD_FIELDS = ("model", "messages", "temperature", "stream")
SENSITIVE_KEYS = {
    "authorization",
    "proxy_authorization",
    "cookie",
    "set_cookie",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "token",
    "access_token",
    "refresh_token",
    "secret",
}
URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
BEARER_PATTERN = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)
CREDENTIAL_PATTERN = re.compile(
    r"\b(api[_ -]?key|authorization|password|token|secret)\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)


def _is_sensitive_key(key):
    normalized = str(key).strip().lower().replace("-", "_")
    return normalized in SENSITIVE_KEYS or normalized.endswith(
        ("_api_key", "_password", "_secret", "_token")
    )


def _sanitize_value(value):
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _is_sensitive_key(key) else _sanitize_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def sanitize_llm_payload(payload):
    if not isinstance(payload, dict):
        raise TypeError("LLM request payload must be a mapping")
    return {
        field: _sanitize_value(payload[field])
        for field in AUDITED_PAYLOAD_FIELDS
        if field in payload
    }


def hash_llm_payload(payload):
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sanitize_error_message(error):
    message = str(error)
    message = BEARER_PATTERN.sub("Bearer [REDACTED]", message)
    message = CREDENTIAL_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED]", message)
    message = URL_PATTERN.sub("[REDACTED_URL]", message)
    return message[:4000]


class LLMRequestAuditObserver:
    def __init__(
        self,
        *,
        project,
        purpose,
        task=None,
        target_id="",
        episode_number=None,
        task_attempt=None,
    ):
        if not isinstance(project, Project):
            project = Project.objects.get(pk=project)
        if task is not None and not isinstance(task, GenerationTask):
            task = GenerationTask.objects.get(pk=task)
        if task is not None and task.project_id != project.id:
            raise ValueError("Generation task does not belong to the audit project")

        self.project = project
        self.task = task
        self.purpose = str(purpose).strip()
        if not self.purpose:
            raise ValueError("LLM request purpose is required")
        self.target_id = str(target_id or (task.target_id if task else ""))
        self.episode_number = episode_number
        self.task_attempt = int(
            task_attempt
            if task_attempt is not None
            else ((task.attempt_count or 1) if task is not None else 1)
        )
        if self.task_attempt < 1:
            raise ValueError("Task attempt must be greater than zero")
        self._sequence = self._existing_max_sequence()
        self._lock = Lock()

    def _history_queryset(self):
        queryset = LLMRequestRecord.objects.filter(
            project=self.project,
            task=self.task,
            purpose=self.purpose,
            target_id=self.target_id,
            episode_number=self.episode_number,
            task_attempt=self.task_attempt,
        )
        if self.task is None:
            queryset = queryset.filter(task__isnull=True)
        return queryset

    def _existing_max_sequence(self):
        return self._history_queryset().aggregate(value=Max("call_sequence"))["value"] or 0

    def request_started(self, payload):
        sanitized_payload = sanitize_llm_payload(payload)
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        record = LLMRequestRecord.objects.create(
            project=self.project,
            task=self.task,
            purpose=self.purpose,
            target_id=self.target_id,
            episode_number=self.episode_number,
            task_attempt=self.task_attempt,
            call_sequence=sequence,
            model=str(sanitized_payload.get("model", "")),
            temperature=sanitized_payload.get("temperature"),
            sanitized_payload=sanitized_payload,
            payload_hash=hash_llm_payload(sanitized_payload),
            status=LLMRequestRecord.STATUS_PENDING,
        )
        return record.pk

    def request_succeeded(self, record_id):
        LLMRequestRecord.objects.filter(pk=record_id).update(
            status=LLMRequestRecord.STATUS_SUCCEEDED,
            completed_at=timezone.now(),
            error_message="",
        )

    def request_failed(self, record_id, error):
        LLMRequestRecord.objects.filter(pk=record_id).update(
            status=LLMRequestRecord.STATUS_FAILED,
            completed_at=timezone.now(),
            error_message=sanitize_error_message(error),
        )


@contextmanager
def audit_llm_requests(
    *,
    project,
    purpose,
    task=None,
    target_id="",
    episode_number=None,
    task_attempt=None,
):
    observer = LLMRequestAuditObserver(
        project=project,
        purpose=purpose,
        task=task,
        target_id=target_id,
        episode_number=episode_number,
        task_attempt=task_attempt,
    )
    with observe_llm_requests(observer):
        yield observer
