import httpx
import pytest
from django.urls import reverse

from studio.models import ModelAssignment, ModelConfig, ProviderConfig
from studio.services.model_config import video_provider_for
from studio.services.model_slots import (
    CATEGORY_VIDEO,
    activate_video_scheme,
    get_video_schemes,
    save_simple_model_slot,
    verify_simple_model_slot,
)


pytestmark = pytest.mark.django_db


def video_slot_payload(base_url, model_id, token, provider_type):
    return {
        "base_url": base_url,
        "model_id": model_id,
        "api_token": token,
        "provider_type": provider_type,
    }


def test_h3_video_scheme_uses_dedicated_provider_and_can_be_globally_activated():
    seedance = save_simple_model_slot(
        CATEGORY_VIDEO,
        video_slot_payload(
            "https://video.example/v1",
            "doubao-seedance-2-0-fast-260128",
            "seedance-secret",
            ProviderConfig.TYPE_OPENAI_COMPATIBLE,
        ),
    )
    h3 = save_simple_model_slot(
        CATEGORY_VIDEO,
        video_slot_payload(
            "https://api.minimaxi.com",
            "MiniMax-H3",
            "h3-secret",
            ProviderConfig.TYPE_MINIMAX_H3,
        ),
    )

    assert seedance.provider_id != h3.provider_id
    assert h3.default_parameters["resolution"] == "768P"
    assert h3.default_parameters["ratio"] == "9:16"
    provider = video_provider_for(h3)
    try:
        assert provider.__class__.__name__ == "MiniMaxH3VideoProvider"
    finally:
        provider.close()
    assert [row["model_id"] for row in get_video_schemes()] == [
        "MiniMax-H3",
        "doubao-seedance-2-0-fast-260128",
    ]

    activate_video_scheme(seedance.id)

    assignment = ModelAssignment.objects.get(purpose=ModelAssignment.PURPOSE_SHOT_VIDEO)
    assert assignment.model_id == seedance.id


def test_h3_configuration_validation_uses_read_only_task_list_endpoint():
    save_simple_model_slot(
        CATEGORY_VIDEO,
        video_slot_payload(
            "https://api.minimaxi.com",
            "MiniMax-H3",
            "h3-secret",
            ProviderConfig.TYPE_MINIMAX_H3,
        ),
    )

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v2/query/video_generation"
        assert request.url.params["page_num"] == "1"
        assert request.url.params["page_size"] == "1"
        assert request.headers["Authorization"] == "Bearer h3-secret"
        return httpx.Response(200, json={"items": [], "total": 0})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = verify_simple_model_slot(CATEGORY_VIDEO, client=client)

    assert model.verification_status == ModelConfig.VERIFICATION_PARTIAL
    assert "H3" in model.verification_message


def test_system_settings_switches_the_global_video_scheme(client):
    seedance = save_simple_model_slot(
        CATEGORY_VIDEO,
        video_slot_payload(
            "https://video.example/v1",
            "doubao-seedance-2-0-fast-260128",
            "seedance-secret",
            ProviderConfig.TYPE_OPENAI_COMPATIBLE,
        ),
    )
    h3 = save_simple_model_slot(
        CATEGORY_VIDEO,
        video_slot_payload(
            "https://api.minimaxi.com",
            "MiniMax-H3",
            "h3-secret",
            ProviderConfig.TYPE_MINIMAX_H3,
        ),
    )
    assert ModelAssignment.objects.get(
        purpose=ModelAssignment.PURPOSE_SHOT_VIDEO
    ).model_id == h3.id

    response = client.post(
        reverse("studio:system_settings"),
        {
            "action": "activate_video_scheme",
            "category": CATEGORY_VIDEO,
            "scheme_id": seedance.id,
        },
    )

    assert response.status_code == 200
    assert "已启用视频方案" in response.content.decode("utf-8")
    assert ModelAssignment.objects.get(
        purpose=ModelAssignment.PURPOSE_SHOT_VIDEO
    ).model_id == seedance.id
    page = response.content.decode("utf-8")
    assert "当前全局方案" in page
    assert "MiniMax-H3" in page
    assert "doubao-seedance-2-0-fast-260128" in page
