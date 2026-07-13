import httpx
import pytest

from studio.llm.image_provider import ImageConfig, ImageProvider
from studio.llm.provider import LLMAPIError


class DeploymentMissingClient:
    def post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(
            404,
            request=request,
            json={
                "error": {
                    "message": "The API deployment for this resource does not exist.",
                    "code": "DeploymentNotFound",
                }
            },
        )


def test_image_provider_explains_missing_gateway_deployment():
    provider = ImageProvider(
        ImageConfig("https://example.test/v1", "key", "gpt-image-2", "/chat/completions", "1024x1536"),
        client=DeploymentMissingClient(),
    )

    with pytest.raises(LLMAPIError) as exc_info:
        provider.generate_image("character")

    assert "gpt-image-2" in str(exc_info.value)
    assert "\u6ca1\u6709\u53ef\u7528\u90e8\u7f72" in str(exc_info.value)
