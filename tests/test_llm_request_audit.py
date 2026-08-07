import json

import pytest
from django.urls import reverse

from studio.llm.provider import LLMAPIError, LLMConfig, LLMProvider
from studio.models import GenerationTask, LLMRequestRecord, Project
from studio.services.llm_request_audit import audit_llm_requests, sanitize_llm_payload


pytestmark = pytest.mark.django_db


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)
        self.content = self.text.encode("utf-8")
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            request = httpx.Request("POST", "https://gateway.test/v1/chat/completions")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("api failed", request=request, response=response)


class FakeClient:
    def __init__(self, response):
        self.response = response

    def post(self, url, headers, json, timeout):
        return self.response


@pytest.fixture
def project():
    return Project.objects.create(
        workspace_id="audit-workspace",
        name="Audit project",
        genre="都市",
        episode_count=60,
        episode_duration_minutes=2,
    )


def make_provider(response):
    return LLMProvider(
        LLMConfig(
            base_url="https://gateway.test/v1",
            api_key="super-secret-key",
            model="model-a",
        ),
        client=FakeClient(response),
    )


def test_audit_context_persists_successful_request_without_transport_secrets(project):
    task = GenerationTask.objects.create(
        project=project,
        task_type=GenerationTask.TYPE_STORYBOARD,
        target_id="12",
        attempt_count=2,
    )
    provider = make_provider(
        FakeResponse(payload={"choices": [{"message": {"content": "{}"}}]})
    )

    with audit_llm_requests(
        project=project,
        task=task,
        purpose="storyboard",
        target_id="episode:12",
        episode_number=12,
        task_attempt=2,
    ):
        provider.generate_json([{"role": "user", "content": "Create shots"}], temperature=0.7)

    record = LLMRequestRecord.objects.get()
    assert record.project == project
    assert record.task == task
    assert record.purpose == "storyboard"
    assert record.target_id == "episode:12"
    assert record.episode_number == 12
    assert record.task_attempt == 2
    assert record.call_sequence == 1
    assert record.status == LLMRequestRecord.STATUS_SUCCEEDED
    assert record.completed_at is not None
    assert record.sanitized_payload == {
        "model": "model-a",
        "messages": [{"role": "user", "content": "Create shots"}],
        "stream": False,
        "temperature": 0.7,
    }
    serialized = json.dumps(record.sanitized_payload)
    assert "super-secret-key" not in serialized
    assert "gateway.test" not in serialized
    assert len(record.payload_hash) == 64


def test_audit_context_uses_a_new_sequence_for_correction_request(project):
    provider = make_provider(
        FakeResponse(payload={"choices": [{"message": {"content": "{}"}}]})
    )

    with audit_llm_requests(project=project, purpose="outline", task_attempt=1):
        provider.generate_json([{"role": "user", "content": "initial"}])
        provider.generate_json([{"role": "user", "content": "correct invalid JSON"}])

    records = list(LLMRequestRecord.objects.order_by("call_sequence"))
    assert [record.call_sequence for record in records] == [1, 2]
    assert records[0].payload_hash != records[1].payload_hash


def test_audit_context_persists_sanitized_failure(project):
    provider = make_provider(FakeResponse(status_code=401, payload={"error": "denied"}))

    with pytest.raises(LLMAPIError):
        with audit_llm_requests(project=project, purpose="script"):
            provider.generate_text([{"role": "user", "content": "write"}])

    record = LLMRequestRecord.objects.get()
    assert record.status == LLMRequestRecord.STATUS_FAILED
    assert record.completed_at is not None
    assert record.error_message
    assert "gateway.test" not in record.error_message
    assert "super-secret-key" not in record.error_message


def test_sanitize_llm_payload_allows_request_body_fields_and_redacts_nested_secrets():
    sanitized = sanitize_llm_payload(
        {
            "model": "model-a",
            "messages": [
                {
                    "role": "user",
                    "content": "story",
                    "authorization": "Bearer hidden",
                }
            ],
            "temperature": 0.4,
            "stream": False,
            "headers": {"Authorization": "Bearer hidden"},
            "url": "https://gateway.test/v1/chat/completions",
            "api_key": "hidden",
        }
    )

    assert sanitized == {
        "model": "model-a",
        "messages": [
            {
                "role": "user",
                "content": "story",
                "authorization": "[REDACTED]",
            }
        ],
        "temperature": 0.4,
        "stream": False,
    }


def test_request_list_is_project_scoped_and_omits_payload(client, project):
    other_project = Project.objects.create(
        workspace_id="other-workspace",
        name="Other",
        genre="悬疑",
        episode_count=60,
        episode_duration_minutes=2,
    )
    own = LLMRequestRecord.objects.create(
        project=project,
        purpose="storyboard",
        target_id="episode:12",
        episode_number=12,
        model="model-a",
        sanitized_payload={"messages": [{"role": "user", "content": "private prompt"}]},
        payload_hash="a" * 64,
        status=LLMRequestRecord.STATUS_SUCCEEDED,
    )
    LLMRequestRecord.objects.create(
        project=other_project,
        purpose="storyboard",
        model="model-b",
        sanitized_payload={"messages": [{"role": "user", "content": "other prompt"}]},
        payload_hash="b" * 64,
    )

    response = client.get(
        reverse("studio:llm_request_list", args=[project.workspace_id]),
        {"purpose": "storyboard", "episode_number": 12},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["requests"]] == [own.id]
    assert "sanitized_payload" not in body["requests"][0]
    assert "private prompt" not in response.content.decode("utf-8")


def test_request_detail_returns_payload_only_within_project(client, project):
    record = LLMRequestRecord.objects.create(
        project=project,
        purpose="outline",
        model="model-a",
        sanitized_payload={"messages": [{"role": "user", "content": "full prompt"}]},
        payload_hash="c" * 64,
    )

    response = client.get(
        reverse("studio:llm_request_detail", args=[project.workspace_id, record.id])
    )
    missing = client.get(
        reverse("studio:llm_request_detail", args=["missing-workspace", record.id])
    )

    assert response.status_code == 200
    assert response.json()["sanitized_payload"] == record.sanitized_payload
    assert missing.status_code == 404


def test_task_status_does_not_embed_audited_request_body(client, project):
    task = GenerationTask.objects.create(
        project=project,
        task_type=GenerationTask.TYPE_STORYBOARD,
        target_id="12",
    )
    LLMRequestRecord.objects.create(
        project=project,
        task=task,
        purpose="storyboard",
        model="model-a",
        sanitized_payload={
            "messages": [{"role": "user", "content": "private audited prompt"}]
        },
        payload_hash="d" * 64,
    )

    response = client.get(reverse("studio:task_status", args=[task.id]))

    assert response.status_code == 200
    assert "private audited prompt" not in response.content.decode("utf-8")
    assert "sanitized_payload" not in response.json()
