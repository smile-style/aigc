import base64

import pytest

from studio.llm.image_provider import ImageConfig, ImageProvider
from studio.services.characters import generate_character_profiles


class FakeResponse:
    status_code = 200
    headers = {}
    content = b""

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeHTTPClient:
    def __init__(self, payload):
        self.payload = payload
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return FakeResponse(self.payload)


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.messages = None

    def generate_json(self, messages, temperature=None):
        self.messages = messages
        return self.payload


def character_payload():
    return {
        "characters": [
            {
                "name": "陈默",
                "role": "重生归来的男主角",
                "appearance": "黑发，轮廓冷峻，身形修长",
                "personality": "克制果断",
                "costume": "黑色风衣与深灰衬衫",
                "image_prompt": "单人全身角色设定图，陈默，黑色风衣，纯色背景",
            }
        ]
    }


def test_generate_character_profiles_returns_editable_image_prompt():
    provider = FakeLLM(character_payload())

    result = generate_character_profiles(
        provider,
        {
            "title": "重生",
            "core_premise": "男主重生",
            "protagonist": "陈默",
            "arc_summary": "逆转命运",
        },
        [{"episode": 1, "title": "醒来", "summary": "发现重生"}],
    )

    assert result[0]["name"] == "陈默"
    assert "单人全身" in result[0]["image_prompt"]
    assert "角色原图" in provider.messages[-1]["content"]


def test_image_provider_accepts_openai_base64_response():
    image_bytes = b"fake-png"
    client = FakeHTTPClient(
        {"data": [{"b64_json": base64.b64encode(image_bytes).decode("ascii")}]}
    )
    provider = ImageProvider(
        ImageConfig(
            base_url="https://example.test/v1",
            api_key="key",
            model="gpt-image-2",
            endpoint="/images/generations",
            size="1024x1536",
        ),
        client=client,
    )

    result = provider.generate_image("character prompt")

    assert result.content == image_bytes
    assert result.model == "gpt-image-2"
    assert client.posts[0][0] == "https://example.test/v1/images/generations"
    assert client.posts[0][1]["json"]["model"] == "gpt-image-2"


@pytest.mark.parametrize("payload", [None, {}, {"characters": []}])
def test_generate_character_profiles_rejects_invalid_payload(payload):
    provider = FakeLLM(payload)

    with pytest.raises(ValueError):
        generate_character_profiles(
            provider,
            {"title": "a", "core_premise": "b", "protagonist": "c", "arc_summary": "d"},
            [],
        )
