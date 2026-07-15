import httpx
import pytest
from django.urls import reverse

from studio.models import ModelAssignment, ModelConfig, ProviderConfig
from studio.services.model_config import provider_api_key, video_provider_for
from studio.services.model_slots import (
    CATEGORY_IMAGE,
    CATEGORY_TEXT,
    CATEGORY_VIDEO,
    save_simple_model_slot,
    verify_simple_model_slot,
)


pytestmark = pytest.mark.django_db


def slot_payload(base_url, model_id, token=""):
    return {
        "base_url": base_url,
        "model_id": model_id,
        "api_token": token,
    }


def video_slot_payload(base_url, model_id, token="", provider_type=""):
    payload = slot_payload(base_url, model_id, token)
    if provider_type:
        payload["provider_type"] = provider_type
    return payload


def test_system_settings_renders_only_three_simple_categories(client):
    response = client.get(reverse("studio:system_settings"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert content.count('class="model-slot model-slot--') == 3
    assert "文本生成模型" in content
    assert "图片生成模型" in content
    assert "视频生成模型" in content
    assert "任务路由" not in content
    assert "模型列表" not in content
    assert "API Key 环境变量" not in content


def test_saving_text_slot_binds_all_text_tasks_and_encrypts_token(client):
    secret = "plain-token-that-must-not-leak"
    response = client.post(
        reverse("studio:system_settings"),
        {
            "action": "save_slot",
            "category": CATEGORY_TEXT,
            **slot_payload("https://text.example/v1", "text-model-v2", secret),
        },
    )

    assert response.status_code == 200
    purposes = (
        ModelAssignment.PURPOSE_OUTLINE,
        ModelAssignment.PURPOSE_SCRIPT,
        ModelAssignment.PURPOSE_EPISODE_SCRIPT,
        ModelAssignment.PURPOSE_CHARACTER_PROFILE,
        ModelAssignment.PURPOSE_STORYBOARD,
    )
    assignments = list(
        ModelAssignment.objects.select_related("model__provider").filter(purpose__in=purposes)
    )
    assert len(assignments) == len(purposes)
    assert {assignment.model_id for assignment in assignments} == {assignments[0].model_id}
    model = assignments[0].model
    assert model.model_id == "text-model-v2"
    assert secret not in model.provider.api_key_ciphertext
    assert provider_api_key(model.provider) == secret
    assert secret not in response.content.decode("utf-8")


def test_blank_token_preserves_saved_secret():
    model = save_simple_model_slot(
        CATEGORY_IMAGE,
        slot_payload("https://image.example/v1", "image-model", "image-secret"),
    )
    ciphertext = model.provider.api_key_ciphertext

    model = save_simple_model_slot(
        CATEGORY_IMAGE,
        slot_payload("https://image.example/v1", "image-model-v2"),
    )
    model.provider.refresh_from_db()

    assert model.provider.api_key_ciphertext == ciphertext
    assert provider_api_key(model.provider) == "image-secret"


def test_text_configuration_validation_succeeds():
    save_simple_model_slot(
        CATEGORY_TEXT,
        slot_payload("https://text.example/v1", "text-model", "text-secret"),
    )

    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer text-secret"
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = verify_simple_model_slot(CATEGORY_TEXT, client=client)

    assert model.verification_status == ModelConfig.VERIFICATION_SUCCESS
    assert model.last_verified_at is not None
    assert model.verification_latency_ms is not None


def test_image_configuration_validation_checks_model_permission():
    save_simple_model_slot(
        CATEGORY_IMAGE,
        slot_payload("https://image.example/v1", "image-model", "image-secret"),
    )

    def handler(request):
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "image-model"}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = verify_simple_model_slot(CATEGORY_IMAGE, client=client)

    assert model.verification_status == ModelConfig.VERIFICATION_SUCCESS


def test_configuration_validation_records_auth_failure():
    save_simple_model_slot(
        CATEGORY_TEXT,
        slot_payload("https://text.example/v1", "text-model", "bad-secret"),
    )

    def handler(request):
        return httpx.Response(401, json={"error": {"message": "invalid token"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = verify_simple_model_slot(CATEGORY_TEXT, client=client)

    assert model.verification_status == ModelConfig.VERIFICATION_FAILED
    assert "Token 无效" in model.verification_message


def test_video_configuration_validation_is_partial_without_creating_task():
    save_simple_model_slot(
        CATEGORY_VIDEO,
        slot_payload(
            "https://video.example/compatible-mode/v1",
            "wan2.7-r2v-2026-06-12",
            "video-secret",
        ),
    )

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/api/v1/tasks/config-validation-check"
        assert request.headers["Authorization"] == "Bearer video-secret"

        return httpx.Response(404, json={"message": "route not found"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = verify_simple_model_slot(CATEGORY_VIDEO, client=client)

    assert model.verification_status == ModelConfig.VERIFICATION_PARTIAL
    assert "首次生成" in model.verification_message


def test_saving_seedance_video_slot_selects_new_api_protocol():
    model = save_simple_model_slot(
        CATEGORY_VIDEO,
        video_slot_payload(
            "https://video.example/v1",
            "doubao-seedance-2-0-fast-260128",
            "video-secret",
        ),
    )

    assert model.provider.provider_type == ProviderConfig.TYPE_OPENAI_COMPATIBLE
    assert model.provider.base_url == "https://video.example/v1"
    provider = video_provider_for(model)
    try:
        assert provider.__class__.__name__ == "NewApiVideoProvider"
    finally:
        provider.close()


def test_seedance_configuration_validation_checks_models_endpoint():
    save_simple_model_slot(
        CATEGORY_VIDEO,
        video_slot_payload(
            "https://video.example/v1",
            "doubao-seedance-2-0-fast-260128",
            "video-secret",
        ),
    )

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={"data": [{"id": "doubao-seedance-2-0-fast-260128"}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = verify_simple_model_slot(CATEGORY_VIDEO, client=client)

    assert model.verification_status == ModelConfig.VERIFICATION_SUCCESS


def test_query_string_action_survives_missing_submit_button_value(client):
    response = client.post(
        f"{reverse('studio:system_settings')}?action=save_slot",
        {
            "category": CATEGORY_TEXT,
            **slot_payload("https://text.example/v1", "query-action-model", "secret"),
        },
    )

    assert response.status_code == 200
    assignment = ModelAssignment.objects.select_related("model").get(
        purpose=ModelAssignment.PURPOSE_OUTLINE
    )
    assert assignment.model.model_id == "query-action-model"
