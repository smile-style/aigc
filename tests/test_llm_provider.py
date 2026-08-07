import json
import logging
import httpx
import pytest

from studio.llm.provider import (
    LLMAPIError,
    LLMConfig,
    LLMConfigurationError,
    LLMJSONParseError,
    LLMProvider,
    observe_llm_requests,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)
        self.content = self.text.encode("utf-8")
        self.headers = {"content-type": "application/json"}

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


class SequenceFakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, headers, json, timeout):
        self.calls.append(
            {"url": url, "headers": headers, "json": json, "timeout": timeout}
        )
        return self.responses.pop(0)


class RecordingObserver:
    def __init__(self):
        self.events = []

    def request_started(self, payload):
        handle = f"request-{len(self.events) + 1}"
        self.events.append(("started", handle, payload))
        return handle

    def request_succeeded(self, handle):
        self.events.append(("succeeded", handle))

    def request_failed(self, handle, error):
        self.events.append(("failed", handle, str(error)))


class ClosingFakeClient(FakeClient):
    def __init__(self, response):
        super().__init__(response)
        self.closed = False

    def close(self):
        self.closed = True


class FakeStreamingResponse(FakeResponse):
    text = (
        'data: {"choices":[{"delta":{"content":"hello"}}]}'
        '\n\n'
        'data: {"choices":[{"delta":{"content":" world"}}]}'
        '\n\n'
        'data: [DONE]'
    )

    def __init__(self):
        super().__init__(payload={}, text=self.text)
        self.headers = {"content-type": "text/event-stream"}


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


def test_provider_default_client_bypasses_environment_proxy(monkeypatch):
    created = {}

    class CapturingClient:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(httpx, "Client", CapturingClient)

    provider = LLMProvider(LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"))

    assert isinstance(provider.client, CapturingClient)
    assert created["trust_env"] is False


def test_provider_default_timeout_is_long_for_generation(monkeypatch):
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)

    provider = LLMProvider(LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"))

    assert provider.timeout == 300


def test_provider_timeout_can_be_configured(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "120.5")

    provider = LLMProvider(LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"))

    assert provider.timeout == 120.5


def test_generate_text_writes_request_log_without_api_key(tmp_path, monkeypatch):
    logging.getLogger("studio.llm").handlers.clear()
    log_path = tmp_path / "llm.log"
    monkeypatch.setenv("LLM_LOG_PATH", str(log_path))
    response = FakeResponse(payload={"choices": [{"message": {"content": "hello"}}]})
    provider = LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="secret-key", model="model-a"),
        client=FakeClient(response),
    )

    assert provider.generate_text([{"role": "user", "content": "Hi"}]) == "hello"

    log_text = log_path.read_text(encoding="utf-8")
    assert "request.start" in log_text
    assert "request.end" in log_text
    assert "response.parsed" in log_text
    assert "https://example.test/v1/chat/completions" in log_text
    assert "secret-key" not in log_text

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
    assert client.calls[0]["json"]["stream"] is False


def test_generate_text_notifies_observer_with_only_final_payload():
    response = FakeResponse(
        payload={"choices": [{"message": {"content": "hello"}}]},
    )
    observer = RecordingObserver()
    provider = LLMProvider(
        LLMConfig(
            base_url="https://example.test/v1",
            api_key="secret-key",
            model="model-a",
        ),
        client=FakeClient(response),
        request_observer=observer,
    )

    provider.generate_text([{"role": "user", "content": "Hi"}], temperature=0.2)

    assert observer.events == [
        (
            "started",
            "request-1",
            {
                "model": "model-a",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
                "temperature": 0.2,
            },
        ),
        ("succeeded", "request-1"),
    ]
    observed_payload = observer.events[0][2]
    assert "secret-key" not in json.dumps(observed_payload)
    assert "url" not in observed_payload
    assert "headers" not in observed_payload


def test_generate_text_marks_observed_request_failed():
    observer = RecordingObserver()
    provider = LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"),
        client=FakeClient(FakeResponse(status_code=401, payload={"error": "bad key"})),
        request_observer=observer,
    )

    with pytest.raises(LLMAPIError):
        provider.generate_text([{"role": "user", "content": "Hi"}])

    assert [event[0] for event in observer.events] == ["started", "failed"]
    assert observer.events[1][1] == "request-1"


def test_transport_retry_is_one_observed_logical_request(monkeypatch):
    monkeypatch.setattr("studio.llm.provider.time.sleep", lambda _seconds: None)
    observer = RecordingObserver()
    client = SequenceFakeClient(
        [
            FakeResponse(status_code=502),
            FakeResponse(payload={"choices": [{"message": {"content": "hello"}}]}),
        ]
    )
    provider = LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"),
        client=client,
        request_observer=observer,
    )

    assert provider.generate_text([{"role": "user", "content": "Hi"}]) == "hello"

    assert len(client.calls) == 2
    assert [event[0] for event in observer.events] == ["started", "succeeded"]


def test_context_observer_applies_only_inside_context():
    response = FakeResponse(payload={"choices": [{"message": {"content": "hello"}}]})
    observer = RecordingObserver()
    provider = LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"),
        client=FakeClient(response),
    )

    with observe_llm_requests(observer):
        provider.generate_text([{"role": "user", "content": "first"}])
    provider.generate_text([{"role": "user", "content": "second"}])

    assert [event[0] for event in observer.events] == ["started", "succeeded"]


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


def test_generate_text_parses_streaming_response_chunks():
    provider = LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"),
        client=FakeClient(FakeStreamingResponse()),
    )

    assert provider.generate_text([{"role": "user", "content": "Hi"}]) == "hello world"


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


@pytest.mark.parametrize("status_code", [502, 503, 504])
def test_generate_text_explains_temporary_gateway_errors(status_code):
    response = FakeResponse(status_code=status_code)
    provider = LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"),
        client=FakeClient(response),
    )

    with pytest.raises(LLMAPIError) as exc_info:
        provider.generate_text([{"role": "user", "content": "Hi"}])

    assert f"HTTP {status_code}" in str(exc_info.value)
    assert "请稍后重试" in str(exc_info.value)


def test_generate_text_wraps_malformed_api_json():
    provider = LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"),
        client=FakeClient(MalformedJSONResponse()),
    )

    with pytest.raises(LLMAPIError):
        provider.generate_text([{"role": "user", "content": "Hi"}])

