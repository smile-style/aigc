import json

import httpx
import pytest

from studio.llm.provider import (
    LLMAPIError,
    LLMConfig,
    LLMConfigurationError,
    LLMJSONParseError,
    LLMProvider,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.test/v1/chat/completions")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("api failed", request=request, response=response)


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self.response


class ClosingFakeClient(FakeClient):
    def __init__(self, response):
        super().__init__(response)
        self.closed = False

    def close(self):
        self.closed = True


class MalformedJSONResponse(FakeResponse):
    def json(self):
        raise json.JSONDecodeError("bad json", doc="{", pos=0)


def test_config_from_env_requires_all_values(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://env.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    monkeypatch.setenv("LLM_MODEL", "env-model")

    with pytest.raises(LLMConfigurationError):
        LLMConfig.from_env({})


def test_provider_from_env_uses_supplied_mapping():
    provider = LLMProvider.from_env(
        {
            "LLM_BASE_URL": "https://mapping.test/v1/",
            "LLM_API_KEY": "mapping-key",
            "LLM_MODEL": "mapping-model",
        }
    )

    assert provider.config == LLMConfig(
        base_url="https://mapping.test/v1",
        api_key="mapping-key",
        model="mapping-model",
    )


def test_generate_text_calls_chat_completions():
    response = FakeResponse(
        payload={"choices": [{"message": {"content": "hello"}}]},
    )
    client = FakeClient(response)
    provider = LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"),
        client=client,
    )

    text = provider.generate_text([{"role": "user", "content": "Hi"}], temperature=0.2)

    assert text == "hello"
    assert client.calls[0]["url"] == "https://example.test/v1/chat/completions"
    assert client.calls[0]["headers"]["Authorization"] == "Bearer key"
    assert client.calls[0]["json"]["model"] == "model-a"
    assert client.calls[0]["json"]["temperature"] == 0.2


def test_close_closes_client_when_supported():
    client = ClosingFakeClient(FakeResponse())
    provider = LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"),
        client=client,
    )

    provider.close()

    assert client.closed is True


def test_context_manager_closes_client_on_exit():
    client = ClosingFakeClient(FakeResponse())

    with LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"),
        client=client,
    ) as provider:
        assert provider.client is client

    assert client.closed is True


def test_generate_json_parses_text_response():
    response = FakeResponse(
        payload={"choices": [{"message": {"content": "{\"ok\": true}"}}]},
    )
    provider = LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"),
        client=FakeClient(response),
    )

    assert provider.generate_json([{"role": "user", "content": "Return JSON"}]) == {"ok": True}


def test_generate_json_raises_for_invalid_json():
    response = FakeResponse(payload={"choices": [{"message": {"content": "not-json"}}]})
    provider = LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"),
        client=FakeClient(response),
    )

    with pytest.raises(LLMJSONParseError):
        provider.generate_json([{"role": "user", "content": "Return JSON"}])


def test_generate_text_wraps_http_errors():
    response = FakeResponse(status_code=401, payload={"error": "bad key"})
    provider = LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"),
        client=FakeClient(response),
    )

    with pytest.raises(LLMAPIError):
        provider.generate_text([{"role": "user", "content": "Hi"}])


def test_generate_text_wraps_malformed_api_json():
    provider = LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"),
        client=FakeClient(MalformedJSONResponse()),
    )

    with pytest.raises(LLMAPIError):
        provider.generate_text([{"role": "user", "content": "Hi"}])
