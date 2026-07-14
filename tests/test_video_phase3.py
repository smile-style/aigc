import base64

import httpx
import pytest
from django.core.files.base import ContentFile
from django.urls import reverse

from studio.llm.video_provider import BailianVideoProvider
from studio.models import (
    Character,
    CharacterAsset,
    Episode,
    GenerationTask,
    ModelAssignment,
    ModelConfig,
    Outline,
    Project,
    Script,
    ShotCharacterReference,
    StoryboardPrompt,
    StoryboardShot,
    VideoAsset,
)
from studio.services.model_config import ensure_default_video_models, normalize_provider_origin
from studio.services.video import (
    effective_video_prompt,
    queue_shot_video,
    reorder_shots,
    save_video_prompt_override,
    storyboard_video_prompt,
    sync_storyboard_shots,
)


pytestmark = pytest.mark.django_db


def make_episode(with_character=True):
    project = Project.objects.create(
        workspace_id="video-workspace",
        name="Video project",
        genre="都市",
        episode_count=60,
        episode_duration_minutes=2,
    )
    outline = Outline.objects.create(
        project=project,
        outline_id="outline-1",
        position=1,
        title="Outline",
        core_premise="Premise",
        protagonist="Lin Mo",
        hook="Hook",
        arc_summary="Arc",
    )
    project.selected_outline = outline
    project.save()
    script = Script.objects.create(project=project, outline=outline, plan_payload=[])
    episode = Episode.objects.create(
        script=script,
        episode_number=1,
        title="Episode 1",
        summary="Summary",
        key_conflict="Conflict",
        cliffhanger="Cliffhanger",
        full_script="Script",
    )
    storyboard = StoryboardPrompt.objects.create(
        project=project,
        script=script,
        episode=episode,
        prompts_payload=[
            {
                "shot_number": 1,
                "duration": "6 秒",
                "visual_description": "林默站在雨中",
                "character_action": "林默抬头",
                "dialogue_or_narration": "林默：开始吧",
                "camera_language": "中景推进",
                "image_prompt": "rainy street",
                "video_prompt": "林默在雨中缓慢抬头，镜头向前推进",
            },
            {
                "shot_number": 2,
                "duration": "4s",
                "visual_description": "空镜",
                "character_action": "雨水落下",
                "dialogue_or_narration": "旁白",
                "camera_language": "固定镜头",
                "image_prompt": "street",
                "video_prompt": "雨夜街道空镜",
            },
        ],
    )
    if with_character:
        character = Character.objects.create(
            script=script,
            name="林默",
            role="主角",
            appearance="黑发",
            image_prompt="portrait",
        )
        asset = CharacterAsset.objects.create(
            character=character,
            model="image-model",
            prompt_snapshot="portrait",
            version=1,
        )
        asset.image.save("lin-mo.png", ContentFile(b"fake-png"))
    return project, episode, storyboard


def test_normalize_video_origin_removes_openai_compatible_path():
    assert normalize_provider_origin("https://example.maas.aliyuncs.com/compatible-mode/v1/") == "https://example.maas.aliyuncs.com"


def test_default_video_models_and_routes_are_seeded():
    provider = ensure_default_video_models()

    assignment = ModelAssignment.objects.select_related("model").get(
        purpose=ModelAssignment.PURPOSE_SHOT_VIDEO
    )
    assert provider.base_url.endswith("cn-beijing.maas.aliyuncs.com")
    assert assignment.model.model_id == "wan2.7-r2v-2026-06-12"
    assert assignment.model.default_parameters["resolution"] == "720P"

def test_default_video_models_migrate_legacy_primary_assignment():
    provider = ensure_default_video_models()
    legacy_model = ModelConfig.objects.create(
        provider=provider,
        name="Legacy Wan R2V",
        model_id="wan2.7-r2v",
        capability=ModelConfig.CAPABILITY_VIDEO_REFERENCE,
        default_parameters={"resolution": "720P"},
    )
    assignment = ModelAssignment.objects.get(purpose=ModelAssignment.PURPOSE_SHOT_VIDEO)
    assignment.model = legacy_model
    assignment.save(update_fields=["model", "updated_at"])

    ensure_default_video_models()

    assignment.refresh_from_db()
    assert assignment.model.model_id == "wan2.7-r2v-2026-06-12"


def test_storyboard_sync_extracts_character_and_duration():
    _, _, storyboard = make_episode()

    shots = sync_storyboard_shots(storyboard)

    assert [shot.duration_seconds for shot in shots] == [6, 4]
    assert shots[0].character_names == ["林默"]
    reference = ShotCharacterReference.objects.get(shot=shots[0])
    assert reference.character.name == "林默"
    assert not shots[1].character_references.exists()


def test_queue_shot_video_pins_model_and_character_asset():
    _, _, storyboard = make_episode()
    ensure_default_video_models()
    shot = sync_storyboard_shots(storyboard)[0]

    asset, created = queue_shot_video(shot)

    assert created is True
    assert asset.status == VideoAsset.STATUS_QUEUED
    assert asset.model_config.model_id == "wan2.7-r2v-2026-06-12"
    assert asset.input_snapshot["references"][0]["asset_version"] == 1
    assert asset.prompt_snapshot == storyboard_video_prompt(shot)
    assert asset.input_snapshot["prompt_policy"] == "full_storyboard_prompt"
    assert asset.input_snapshot["source_storyboard_prompt"] == storyboard_video_prompt(shot)
    assert "**画面描述**" in asset.prompt_snapshot
    assert "**对白 / 旁白**" in asset.prompt_snapshot
    assert "**图片 Prompt**" in asset.prompt_snapshot
    assert "**视频 Prompt**" in asset.prompt_snapshot
    assert asset.input_snapshot["parameters"]["prompt_extend"] is False
    assert asset.generation_task.task_type == GenerationTask.TYPE_SHOT_VIDEO




def test_queue_shot_video_uses_manual_prompt_override():
    _, _, storyboard = make_episode()
    ensure_default_video_models()
    shot = sync_storyboard_shots(storyboard)[0]
    custom_prompt = "人工调整后的完整视频 Prompt"

    save_video_prompt_override(shot, custom_prompt)
    asset, created = queue_shot_video(shot)

    assert created is True
    assert shot.video_prompt_override == custom_prompt
    assert effective_video_prompt(shot) == custom_prompt
    assert asset.prompt_snapshot == custom_prompt
    assert asset.input_snapshot["prompt_policy"] == "manual_override"

def test_video_provider_uses_dashscope_async_endpoint(tmp_path):
    provider_config = ensure_default_video_models()
    model = ModelConfig.objects.get(model_id="wan2.7-r2v-2026-06-12")
    image_path = tmp_path / "character.png"
    image_path.write_bytes(b"image")

    def handler(request):
        assert request.url.path == "/api/v1/services/aigc/video-generation/video-synthesis"
        assert request.headers["X-DashScope-Async"] == "enable"
        payload = __import__("json").loads(request.content)
        assert payload["input"]["prompt"] == "prompt"
        assert payload["parameters"]["prompt_extend"] is False
        assert payload["model"] == "wan2.7-r2v-2026-06-12"
        assert payload["parameters"]["resolution"] == "720P"
        assert payload["input"]["media"][0]["url"].startswith("data:image/png;base64,")
        return httpx.Response(200, json={"output": {"task_id": "task-1"}, "request_id": "request-1"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = BailianVideoProvider(model, "secret", client=client)
    result = provider.submit_reference_video("prompt", [image_path], {"duration": 6})

    assert result.task_id == "task-1"
    assert base64.b64encode(b"image").decode() in provider._file_data_uri(image_path)


def test_system_settings_page_loads_seeded_models(client):
    response = client.get(reverse("studio:system_settings"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "系统管理" in content
    assert "wan2.7-r2v-2026-06-12" in content
    assert "文本生成模型" in content
    assert "任务路由" not in content


def test_video_page_loads_shots_and_character_reference(client):
    project, _, _ = make_episode()

    response = client.get(reverse("studio:video_episode", args=[project.workspace_id, 1]))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "视频成片" in content
    assert "镜头 1" in content
    assert "林默" in content

    assert "当前生成 Prompt" in content
    assert "来自分镜 · 六段完整内容" in content
    assert "编辑 Prompt" in content
    assert "**画面描述**" in content
    assert "6 秒 · 中景推进" not in content



def test_video_prompt_can_be_saved_and_reset(client):
    project, _, storyboard = make_episode()
    shot = sync_storyboard_shots(storyboard)[0]
    url = reverse(
        "studio:save_shot_video_prompt",
        args=[project.workspace_id, 1, shot.shot_id],
    )

    response = client.post(
        url,
        {"action": "save", "prompt": "人工工作稿"},
    )

    assert response.status_code == 302
    shot.refresh_from_db()
    assert shot.video_prompt_override == "人工工作稿"
    page = client.get(reverse("studio:video_episode", args=[project.workspace_id, 1]))
    assert "人工修改" in page.content.decode("utf-8")
    assert "人工工作稿" in page.content.decode("utf-8")

    response = client.post(url, {"action": "reset"})

    assert response.status_code == 302
    shot.refresh_from_db()
    assert shot.video_prompt_override == ""
    assert effective_video_prompt(shot) == storyboard_video_prompt(shot)


def test_reorder_shots_updates_production_order():
    _, episode, storyboard = make_episode()
    shots = sync_storyboard_shots(storyboard)

    reorder_shots(episode, [str(shots[1].shot_id), str(shots[0].shot_id)])

    assert list(StoryboardShot.objects.values_list("shot_number", flat=True)) == [2, 1]
    sync_storyboard_shots(storyboard)

    assert list(StoryboardShot.objects.values_list("shot_number", flat=True)) == [2, 1]


def test_video_status_does_not_resync_or_write(client, monkeypatch):
    project, _, _ = make_episode()
    calls = []

    def fake_page_data(episode, sync=True):
        calls.append(sync)
        return {
            "counts": {"total": 0, "ready": 0, "running": 0, "failed": 0},
            "shots": [],
            "composition": None,
        }

    monkeypatch.setattr("studio.video_views.video_page_data", fake_page_data)
    response = client.get(reverse("studio:video_status", args=[project.workspace_id, 1]))

    assert response.status_code == 200
    assert calls == [False]
