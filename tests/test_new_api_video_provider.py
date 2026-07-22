import base64
import json
from types import SimpleNamespace

import httpx

from studio.llm.new_api_video_provider import NewApiVideoProvider


def model_config(base_url="https://video.example/v1"):
    provider = SimpleNamespace(base_url=base_url, timeout_seconds=600)
    return SimpleNamespace(
        model_id="doubao-seedance-2-0-fast-260128",
        provider=provider,
        default_parameters={
            "resolution": "720P",
            "ratio": "9:16",
            "duration": 5,
            "watermark": False,
            "generate_audio": False,
        },
    )


def test_submit_prompt_only_omits_images():
    def handler(request):
        assert request.url.path == "/v1/video/generations"
        payload = json.loads(request.content)
        assert payload["prompt"] == "full storyboard prompt"
        assert payload["seconds"] == "8"
        assert payload["metadata"]["resolution"] == "720p"
        assert "images" not in payload
        return httpx.Response(200, json={"task_id": "task-1", "status": "queued"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = NewApiVideoProvider(model_config(), "secret", client=client)

    submission = provider.submit_video(
        "full storyboard prompt",
        [],
        parameters={"duration": 8},
    )

    assert submission.task_id == "task-1"


def test_seedance_clamps_short_duration_to_four_seconds():
    submitted_seconds = []

    def handler(request):
        payload = json.loads(request.content)
        submitted_seconds.append(payload["seconds"])
        return httpx.Response(200, json={"task_id": f"task-{len(submitted_seconds)}"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = NewApiVideoProvider(model_config(), "secret", client=client)

    provider.submit_video("prompt", parameters={"duration": 2})
    provider.submit_video("prompt", parameters={"duration": 3})
    provider.submit_video("prompt", parameters={"duration": 4})

    assert submitted_seconds == ["4", "4", "4"]


def test_submit_with_reference_images_uses_data_uris(tmp_path):
    image_path = tmp_path / "character.png"
    image_path.write_bytes(b"image")

    def handler(request):
        payload = json.loads(request.content)
        assert "images" not in payload
        content = payload["metadata"]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "image_url"
        assert content[0]["role"] == "reference_image"
        image_url = content[0]["image_url"]["url"]
        assert image_url.startswith("data:image/png;base64,")
        assert base64.b64encode(b"image").decode("ascii") in image_url
        return httpx.Response(200, json={"id": "task-2"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = NewApiVideoProvider(model_config(), "secret", client=client)

    submission = provider.submit_video("prompt", [image_path])

    assert submission.task_id == "task-2"


def test_get_task_result_normalizes_gateway_response():
    def handler(request):
        assert request.url.path == "/v1/video/generations/task-1"
        return httpx.Response(
            200,
            json={
                "code": "success",
                "data": {
                    "status": "SUCCESS",
                    "result_url": "https://cdn.example/video.mp4",
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = NewApiVideoProvider(model_config(), "secret", client=client)

    result = provider.get_task_result("task-1")

    assert result.status == "succeeded"
    assert result.video_url == "https://cdn.example/video.mp4"
