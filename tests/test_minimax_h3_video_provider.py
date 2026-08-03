import base64
import json
from types import SimpleNamespace

import httpx

from studio.llm.minimax_h3_video_provider import MiniMaxH3VideoProvider


def model_config(base_url="https://api.minimaxi.com"):
    provider = SimpleNamespace(base_url=base_url, timeout_seconds=600)
    return SimpleNamespace(
        model_id="MiniMax-H3",
        provider=provider,
        default_parameters={
            "resolution": "768P",
            "ratio": "9:16",
            "duration": 5,
            "watermark": False,
        },
    )


def test_submit_reference_video_uses_h3_multimodal_content(tmp_path):
    image_path = tmp_path / "character.png"
    image_path.write_bytes(b"image")

    def handler(request):
        assert request.url.path == "/v2/video_generation"
        assert request.headers["Authorization"] == "Bearer secret"
        payload = json.loads(request.content)
        assert payload == {
            "model": "MiniMax-H3",
            "content": [
                {"type": "text", "text": "prompt\n\n避免：blur"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.b64encode(b"image").decode("ascii")
                    },
                    "role": "reference_image",
                },
            ],
            "resolution": "768P",
            "duration": 4,
            "ratio": "9:16",
            "aigc_watermark": False,
        }
        return httpx.Response(200, json={"task_id": "h3-task-1"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MiniMaxH3VideoProvider(model_config(), "secret", client=client)

    submission = provider.submit_video(
        "prompt",
        [image_path],
        parameters={"duration": 2, "resolution": "720P"},
        negative_prompt="blur",
    )

    assert submission.task_id == "h3-task-1"


def test_get_task_result_normalizes_h3_response():
    def handler(request):
        assert request.url.path == "/v2/query/video_generation/h3-task-1"
        return httpx.Response(
            200,
            json={
                "task": {
                    "id": "h3-task-1",
                    "status": "succeeded",
                    "content": {"url": "https://cdn.example/h3.mp4"},
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MiniMaxH3VideoProvider(model_config(), "secret", client=client)

    result = provider.get_task_result("h3-task-1")

    assert result.status == "succeeded"
    assert result.video_url == "https://cdn.example/h3.mp4"


def test_get_task_result_surfaces_h3_failure_message():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "task": {
                    "id": "h3-task-2",
                    "status": "failed",
                    "error": {"code": "1026", "message": "sensitive content"},
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MiniMaxH3VideoProvider(model_config(), "secret", client=client)

    result = provider.get_task_result("h3-task-2")

    assert result.status == "failed"
    assert result.message == "sensitive content"
