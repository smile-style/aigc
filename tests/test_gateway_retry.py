import json

import httpx

from studio.llm.image_provider import ImageConfig, ImageProvider
from studio.llm.provider import LLMConfig, LLMProvider


class FakeResponse:
    headers = {"content-type": "application/json"}

    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload or {}
        self.content = json.dumps(self.payload).encode("utf-8")
        self.text = self.content.decode("utf-8")

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.test/v1/chat/completions")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("gateway error", request=request, response=response)


class SequenceClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def post(self, *args, **kwargs):
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


def test_text_provider_retries_temporary_gateway_error(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    monkeypatch.setattr("studio.llm.provider.time.sleep", lambda seconds: None)
    client = SequenceClient(
        [
            FakeResponse(502),
            FakeResponse(200, {"choices": [{"message": {"content": "OK"}}]}),
        ]
    )
    provider = LLMProvider(
        LLMConfig("https://example.test/v1", "key", "model-a"),
        client=client,
    )

    result = provider.generate_text([{"role": "user", "content": "Hi"}])

    assert result == "OK"
    assert client.calls == 2


def test_image_provider_retries_temporary_gateway_error(monkeypatch):
    monkeypatch.setenv("IMAGE_MAX_RETRIES", "3")
    monkeypatch.setattr("studio.llm.image_provider.time.sleep", lambda seconds: None)
    client = SequenceClient(
        [
            FakeResponse(503),
            FakeResponse(200, {"data": [{"b64_json": "aW1hZ2U="}]}),
        ]
    )
    provider = ImageProvider(
        ImageConfig(
            "https://example.test/v1",
            "key",
            "gpt-image-2",
            "/chat/completions",
            "1024x1536",
        ),
        client=client,
    )

    result = provider.generate_image("character")

    assert result.content == b"image"
    assert client.calls == 2
