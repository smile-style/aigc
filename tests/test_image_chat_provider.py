import base64

from studio.llm.image_provider import ImageConfig, ImageProvider


class FakeResponse:
    headers = {}
    content = b""

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return FakeResponse(self.payload)


def test_gpt_image_legacy_endpoint_maps_to_chat_completions():
    config = ImageConfig.from_env(
        {
            "LLM_BASE_URL": "https://example.test/v1",
            "LLM_API_KEY": "key",
            "IMAGE_MODEL": "gpt-image-2",
            "IMAGE_API_PATH": "/images/generations",
        }
    )

    assert config.endpoint == "/chat/completions"


def test_chat_completions_request_and_data_uri_response():
    image_bytes = b"chat-image"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    client = FakeClient(
        {"choices": [{"message": {"content": f"data:image/png;base64,{encoded}"}}]}
    )
    provider = ImageProvider(
        ImageConfig(
            base_url="https://example.test/v1",
            api_key="key",
            model="gpt-image-2",
            endpoint="/chat/completions",
            size="1024x1536",
        ),
        client=client,
    )

    result = provider.generate_image("character prompt")

    url, request = client.posts[0]
    assert result.content == image_bytes
    assert url == "https://example.test/v1/chat/completions"
    assert request["json"]["model"] == "gpt-image-2"
    assert request["json"]["messages"][0]["content"].startswith("character prompt")
    assert request["json"]["messages"][0]["content"].endswith("1024x1536")
    assert request["json"]["stream"] is False


def test_generate_image_can_override_size_for_cover_assets():
    encoded = base64.b64encode(b"wide-cover").decode("ascii")
    client = FakeClient(
        {"choices": [{"message": {"content": f"data:image/png;base64,{encoded}"}}]}
    )
    provider = ImageProvider(
        ImageConfig(
            base_url="https://example.test/v1",
            api_key="key",
            model="gpt-image-2",
            endpoint="/chat/completions",
            size="1024x1536",
        ),
        client=client,
    )

    provider.generate_image("wide cover prompt", size="1536x1024")

    _, request = client.posts[0]
    assert request["json"]["messages"][0]["content"].endswith("1536x1024")
