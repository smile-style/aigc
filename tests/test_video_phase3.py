import base64
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
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
    ShotSubtitleSetting,
    StoryboardPrompt,
    StoryboardShot,
    SubtitleTrack,
    VideoAsset,
    VideoComposition,
)
from studio.services.model_config import ensure_default_video_models, normalize_provider_origin
from studio.services.subtitles import (
    generate_subtitle_cues,
    process_subtitle_task,
    recognize_speech_alignment,
    render_ass,
    render_srt,
    retime_subtitle_snapshot,
    retime_subtitle_snapshot_for_edit_plan,
    save_shot_subtitle,
    save_subtitle_style,
    save_subtitle_track,
    split_dialogue,
    subtitle_snapshot,
    subtitle_source_hash,
)
from studio.services.video import (
    build_edit_plan,
    effective_video_prompt,
    queue_episode_videos,
    queue_export,
    queue_shot_video,
    reorder_shots,
    save_video_prompt_override,
    storyboard_video_prompt,
    sync_latest_character_assets,
    sync_storyboard_shots,
    _ffmpeg_concat,
)
from studio.video_models import composition_video_upload_to, shot_video_upload_to


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


def test_queue_shot_video_allows_prompt_only_when_no_character_reference():
    _, _, storyboard = make_episode()
    ensure_default_video_models()
    shot = sync_storyboard_shots(storyboard)[1]

    asset, created = queue_shot_video(shot)

    assert created is True
    assert asset.status == VideoAsset.STATUS_QUEUED
    assert asset.input_snapshot["references"] == []
    assert asset.prompt_snapshot == storyboard_video_prompt(shot)


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


def test_sync_latest_character_assets_updates_future_video_references():
    _, episode, storyboard = make_episode()
    shot = sync_storyboard_shots(storyboard)[0]
    reference = ShotCharacterReference.objects.get(shot=shot)
    latest = CharacterAsset.objects.create(
        character=reference.character,
        model="image-model",
        prompt_snapshot="new portrait",
        version=2,
    )
    latest.image.save("lin-mo-v2.png", ContentFile(b"new-png"))

    updated = sync_latest_character_assets(episode)

    reference.refresh_from_db()
    assert updated == 1
    assert reference.asset_id == latest.id


def test_one_click_generation_queues_missing_and_failed_but_skips_ready():
    _, episode, storyboard = make_episode(with_character=False)
    shots = sync_storyboard_shots(storyboard)
    ensure_default_video_models()
    ready, _ = queue_shot_video(shots[0])
    ready.status = VideoAsset.STATUS_READY
    ready.save(update_fields=["status"])
    failed, _ = queue_shot_video(shots[1])
    failed.status = VideoAsset.STATUS_FAILED
    failed.save(update_fields=["status"])

    queued, errors = queue_episode_videos(episode)

    assert errors == []
    assert len(queued) == 1
    assert queued[0].shot_id == shots[1].id
    assert queued[0].version == 2


def test_video_storage_paths_include_script_episode_shot_and_version():
    _, episode, storyboard = make_episode(with_character=False)
    storyboard.script.outline.title = "My Story"
    storyboard.script.outline.save(update_fields=["title"])
    shot = sync_storyboard_shots(storyboard)[0]
    asset = VideoAsset(shot=shot, version=3)
    composition = VideoComposition(episode=episode, version=2)

    assert shot_video_upload_to(asset, "result.mp4").endswith(
        f"S{storyboard.script.id:06d}-my-story/episode-001/shots/shot-001/v003.mp4"
    )
    assert composition_video_upload_to(composition, "result.mp4").endswith(
        f"S{storyboard.script.id:06d}-my-story/episode-001/compositions/v002.mp4"
    )


def test_each_export_creates_a_new_composition_version():
    _, episode, storyboard = make_episode(with_character=False)
    ensure_default_video_models()
    for shot in sync_storyboard_shots(storyboard):
        asset, _ = queue_shot_video(shot)
        asset.status = VideoAsset.STATUS_READY
        asset.is_selected = True
        asset.save(update_fields=["status", "is_selected"])

    first_task, created = queue_export(episode)
    assert created is True
    active_task, created = queue_export(episode)
    assert created is False
    assert active_task.id == first_task.id

    first_task.status = GenerationTask.STATUS_SUCCEEDED
    first_task.save(update_fields=["status"])
    second_task, created = queue_export(episode)

    assert created is True
    assert list(
        VideoComposition.objects.filter(episode=episode)
        .order_by("version")
        .values_list("version", flat=True)
    ) == [1, 2]
    assert first_task.target_id != second_task.target_id


def test_queue_export_freezes_cold_open_trim_and_reuses_the_source_asset():
    _, episode, storyboard = make_episode(with_character=False)
    shots = sync_storyboard_shots(storyboard)
    assets = []
    for index, shot in enumerate(shots, start=1):
        asset = VideoAsset.objects.create(
            shot=shot,
            version=2,
            status=VideoAsset.STATUS_READY,
            prompt_snapshot="prompt",
            is_selected=True,
        )
        asset.video.save(f"cold-open-{index}.mp4", ContentFile(b"video"))
        assets.append(asset)
    storyboard.cold_open_payload = {
        "hook_type": "reversal_dialogue",
        "source_beat_id": "beat_05_resolution_or_reversal",
        "source_shot_number": 2,
        "duration_seconds": 3,
        "withheld_reveal": "答案",
        "return_bridge": "两小时前",
        "trim_start_ms": 500,
        "trim_end_ms": 3500,
    }
    storyboard.save(update_fields=["cold_open_payload", "updated_at"])
    sync_storyboard_shots(storyboard)

    task, created = queue_export(episode, include_subtitles=False)
    composition = VideoComposition.objects.get(pk=task.target_id)

    assert created is True
    assert len(composition.edit_plan) == len(assets) + 1
    assert composition.edit_plan[0]["role"] == "cold_open"
    assert composition.edit_plan[0]["asset_id"] == assets[1].id
    assert composition.edit_plan[0]["out_ms"] - composition.edit_plan[0]["in_ms"] == 3000
    assert composition.edit_plan[-1]["asset_id"] == assets[1].id
    assert task.input_snapshot["video_asset_ids"] == [asset.id for asset in assets]
    assert task.input_snapshot["edit_plan_hash"]


def test_ffmpeg_export_preserves_audio_without_subtitles(tmp_path, monkeypatch):
    source = tmp_path / "shot.mp4"
    source.write_bytes(b"source")
    asset = SimpleNamespace(
        video=SimpleNamespace(path=str(source)),
        shot=SimpleNamespace(
            dialogue_or_narration="林默：开始吧",
            duration_seconds=6,
        ),
    )
    commands = []

    monkeypatch.setattr("studio.services.video.shutil.which", lambda name: name)

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == "ffprobe":
            return SimpleNamespace(stdout="0\n")
        Path(command[-1]).write_bytes(b"exported-video")
        return SimpleNamespace(stdout=b"", stderr=b"")

    monkeypatch.setattr("studio.services.video.subprocess.run", fake_run)

    content = _ffmpeg_concat([asset])

    normalize = next(command for command in commands if "-vf" in command)
    video_filter = normalize[normalize.index("-vf") + 1]
    assert content == b"exported-video"
    assert "-an" not in normalize
    assert normalize[normalize.index("-map") + 1] == "0:v:0"
    assert "0:a:0" in normalize
    assert normalize[normalize.index("-c:a") + 1] == "aac"
    assert "subtitles=" not in video_filter


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


def test_video_page_enables_generation_without_character_reference(client):
    project, _, _ = make_episode()

    response = client.get(reverse("studio:video_episode", args=[project.workspace_id, 1]))

    content = response.content.decode("utf-8")
    shot_two = content.split('data-video-shot="', 2)[2].split("</article>", 1)[0]
    button = shot_two.split("生成本镜头", 1)[0].rsplit("<button", 1)[1]
    assert "disabled" not in button


def test_ready_composition_can_be_downloaded_and_reexported(client):
    project, episode, _ = make_episode()
    VideoComposition.objects.create(
        episode=episode,
        version=1,
        status=VideoComposition.STATUS_READY,
        video="videos/exports/existing.mp4",
    )

    response = client.get(
        reverse("studio:video_episode", args=[project.workspace_id, 1]),
        {"tab": "assembly"},
    )

    content = response.content.decode("utf-8")
    assert "variant=clean" in content
    assert "&#19979;&#36733;&#26080;&#23383;&#24149;&#29256;&#26412;" in content
    assert "&#19979;&#36733;&#26377;&#23383;&#24149;&#29256;&#26412;" in content
    assert "disabled" in content


def test_external_captioned_video_upload_creates_ready_version(client, settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path
    project, episode, _ = make_episode(with_character=False)
    clean = VideoComposition.objects.create(
        episode=episode,
        version=1,
        variant=VideoComposition.VARIANT_CLEAN,
        status=VideoComposition.STATUS_READY,
        video_duration_ms=10000,
        video_width=720,
        video_height=1280,
    )
    clean.video.save("clean.mp4", ContentFile(b"clean-video"))
    monkeypatch.setattr(
        "studio.services.video._probe_video_metadata",
        lambda path: {
            "duration_ms": 10400,
            "video_codec": "h264",
            "audio_codec": "aac",
            "width": 1080,
            "height": 1920,
        },
    )

    response = client.post(
        reverse(
            "studio:upload_external_captioned_video",
            args=[project.workspace_id, episode.episode_number],
        ),
        {
            "script_id": episode.script_id,
            "video": SimpleUploadedFile(
                "captioned-final.mp4",
                b"external-captioned-video",
                content_type="video/mp4",
            ),
        },
    )

    assert response.status_code == 302
    assert "external_uploaded=2" in response.url
    composition = episode.video_compositions.get(version=2)
    assert composition.variant == VideoComposition.VARIANT_CAPTIONED
    assert composition.source == VideoComposition.SOURCE_EXTERNAL_UPLOAD
    assert composition.status == VideoComposition.STATUS_READY
    assert composition.include_subtitles is True
    assert composition.original_filename == "captioned-final.mp4"
    assert composition.video_duration_ms == 10400
    assert composition.video_width == 1080
    assert composition.video_height == 1920
    assert len(composition.content_hash) == 64
    with composition.video.open("rb") as uploaded:
        assert uploaded.read() == b"external-captioned-video"


def test_external_captioned_video_upload_rejects_duration_mismatch(client, settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path
    project, episode, _ = make_episode(with_character=False)
    clean = VideoComposition.objects.create(
        episode=episode,
        version=1,
        variant=VideoComposition.VARIANT_CLEAN,
        status=VideoComposition.STATUS_READY,
        video_duration_ms=10000,
    )
    clean.video.save("clean.mp4", ContentFile(b"clean-video"))
    monkeypatch.setattr(
        "studio.services.video._probe_video_metadata",
        lambda path: {
            "duration_ms": 12000,
            "video_codec": "h264",
            "audio_codec": "aac",
            "width": 720,
            "height": 1280,
        },
    )

    response = client.post(
        reverse(
            "studio:upload_external_captioned_video",
            args=[project.workspace_id, episode.episode_number],
        ),
        {
            "script_id": episode.script_id,
            "video": SimpleUploadedFile("captioned.mp4", b"video", content_type="video/mp4"),
        },
    )

    assert response.status_code == 400
    assert "???? 2.00 ?" in response.content.decode("utf-8")
    assert not episode.video_compositions.filter(
        source=VideoComposition.SOURCE_EXTERNAL_UPLOAD
    ).exists()


def test_assembly_page_shows_external_caption_upload_and_version_source(client):
    project, episode, _ = make_episode(with_character=False)
    composition = VideoComposition.objects.create(
        episode=episode,
        version=1,
        variant=VideoComposition.VARIANT_CAPTIONED,
        status=VideoComposition.STATUS_READY,
        source=VideoComposition.SOURCE_EXTERNAL_UPLOAD,
        video="videos/external-captioned.mp4",
        include_subtitles=True,
    )

    response = client.get(
        reverse("studio:video_episode", args=[project.workspace_id, 1]),
        {"tab": "assembly"},
    )
    content = response.content.decode("utf-8")

    assert reverse(
        "studio:upload_external_captioned_video",
        args=[project.workspace_id, episode.episode_number],
    ) in content
    assert "&#22806;&#37096;&#23383;&#24149;&#29256;" in content
    assert reverse("studio:download_finished_film", args=[composition.id]) in content


def test_assembly_page_identifies_shots_blocking_export(client):
    from studio.services.video import video_page_data

    project, episode, storyboard = make_episode(with_character=False)
    shots = sync_storyboard_shots(storyboard)
    asset = VideoAsset.objects.create(
        shot=shots[0],
        version=1,
        status=VideoAsset.STATUS_READY,
        prompt_snapshot="prompt",
        is_selected=True,
    )
    asset.video.save("ready-shot.mp4", ContentFile(b"video"))

    data = video_page_data(episode)
    response = client.get(
        reverse("studio:video_episode", args=[project.workspace_id, 1]),
        {"tab": "assembly"},
    )
    content = response.content.decode("utf-8")

    assert data["counts"]["missing_shot_numbers"] == [2]
    assert "data-export-blocker" in content
    export_url = reverse("studio:export_video", args=[project.workspace_id, 1])
    export_form = content.split(f'action="{export_url}"', 1)[1].split("</form>", 1)[0]
    button = export_form.split("<button", 1)[1].split("</button>", 1)[0]
    assert "disabled" in button


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


def test_video_page_does_not_resync_existing_shots(client, monkeypatch):
    project, _, storyboard = make_episode()
    sync_storyboard_shots(storyboard)

    def fail_if_called(episode):
        raise AssertionError("existing shots must make the page read-only")

    monkeypatch.setattr("studio.services.video.sync_episode_shots", fail_if_called)

    response = client.get(reverse("studio:video_episode", args=[project.workspace_id, 1]))

    assert response.status_code == 200


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

def test_subtitle_generation_uses_storyboard_text_and_shot_offsets():
    _, episode, storyboard = make_episode(with_character=False)
    shots = sync_storyboard_shots(storyboard)
    assets = []
    for index, shot in enumerate(shots, start=1):
        asset = VideoAsset.objects.create(
            shot=shot,
            version=1,
            status=VideoAsset.STATUS_READY,
            prompt_snapshot="prompt",
            is_selected=True,
        )
        asset.video.save(f"subtitle-shot-{index}.mp4", ContentFile(b"not-a-real-video"))
        assets.append(asset)

    track = SubtitleTrack.objects.create(episode=episode)
    generate_subtitle_cues(track, assets, source_hash="source-hash")

    cues = list(track.cues.order_by("position"))
    assert track.status == SubtitleTrack.STATUS_NEEDS_REVIEW
    assert track.source_hash == "source-hash"
    assert cues[0].source_text == "\u6797\u9ed8\uff1a\u5f00\u59cb\u5427"
    assert cues[0].text == "\u5f00\u59cb\u5427"
    assert track.qc_status == SubtitleTrack.QC_PASSED
    assert track.qc_checked_at is not None
    assert track.qc_content_hash
    assert len(cues) == 1
    assert cues[0].start_ms < cues[0].end_ms <= 6000
    assert cues[0].local_start_ms < cues[0].local_end_ms <= 6000
    assert cues[0].needs_review is True
    assert ShotSubtitleSetting.objects.filter(
        shot=shots[0],
        status=ShotSubtitleSetting.STATUS_NEEDS_REVIEW,
    ).exists()
    assert ShotSubtitleSetting.objects.filter(
        shot=shots[1],
        status=ShotSubtitleSetting.STATUS_CONFIRMED,
    ).exists()

def test_subtitle_renderers_emit_bottom_center_ass_and_srt():
    snapshot = {
        "style": {
            "font_name": "Noto Sans CJK SC",
            "font_size": 38,
            "text_color": "#FFFFFF",
            "outline_color": "#000000",
            "outline_size": 3,
            "margin_bottom": 92,
        },
        "cues": [
            {"position": 1, "text": "first cue", "start_ms": 1200, "end_ms": 3400},
        ],
    }

    srt = render_srt(snapshot)
    ass = render_ass(snapshot)

    assert "00:00:01,200 --> 00:00:03,400" in srt
    assert "Alignment" in ass
    assert ",2,46,46,92,1" in ass
    assert "Dialogue: 0,0:00:01.20,0:00:03.40" in ass


def test_subtitle_save_rejects_overlapping_cues():
    _, episode, storyboard = make_episode(with_character=False)
    shots = sync_storyboard_shots(storyboard)
    track = SubtitleTrack.objects.create(episode=episode)
    first = track.cues.create(
        shot=shots[0],
        position=1,
        source_text="first",
        text="first",
        start_ms=0,
        end_ms=2000,
    )
    second = track.cues.create(
        shot=shots[0],
        position=2,
        source_text="second",
        text="second",
        start_ms=2200,
        end_ms=4000,
    )

    with pytest.raises(ValueError):
        save_subtitle_track(
            track,
            enabled=True,
            global_offset_ms=0,
            cues=[
                {"id": first.id, "text": first.text, "start_ms": 0, "end_ms": 2500, "reviewed": True},
                {"id": second.id, "text": second.text, "start_ms": 2400, "end_ms": 4000, "reviewed": True},
            ],
        )


def test_blank_subtitle_is_hidden_and_does_not_overlap_or_export():
    _, episode, storyboard = make_episode(with_character=False)
    shot = sync_storyboard_shots(storyboard)[0]
    track = SubtitleTrack.objects.create(
        episode=episode,
        style_options={"font_size": 44},
    )
    first = track.cues.create(
        shot=shot,
        position=1,
        source_text="first",
        recognized_text="recognized first",
        text="first",
        start_ms=0,
        end_ms=2000,
        needs_review=True,
    )
    second = track.cues.create(
        shot=shot,
        position=2,
        source_text="second",
        text="second",
        start_ms=2200,
        end_ms=4000,
        needs_review=True,
    )

    save_subtitle_track(
        track,
        enabled=True,
        global_offset_ms=0,
        cues=[
            {"id": first.id, "text": "", "start_ms": 0, "end_ms": 2500, "reviewed": False},
            {"id": second.id, "text": "second", "start_ms": 2400, "end_ms": 4000, "reviewed": True},
        ],
    )

    first.refresh_from_db()
    track.refresh_from_db()
    snapshot = subtitle_snapshot(track)

    assert first.text == ""
    assert first.recognized_text == "recognized first"
    assert first.needs_review is False
    assert track.style_options == {"font_size": 44}
    assert [cue["text"] for cue in snapshot["cues"]] == ["second"]
    assert "first" not in render_srt(snapshot)


def test_subtitle_snapshot_applies_global_offset():
    _, episode, storyboard = make_episode(with_character=False)
    shot = sync_storyboard_shots(storyboard)[0]
    track = SubtitleTrack.objects.create(episode=episode, global_offset_ms=-200)
    track.cues.create(
        shot=shot,
        position=1,
        source_text="start",
        text="start",
        start_ms=100,
        end_ms=1000,
    )

    snapshot = subtitle_snapshot(track)

    assert snapshot["cues"][0]["start_ms"] == 0
    assert snapshot["cues"][0]["end_ms"] == 800


def test_subtitle_snapshot_follows_reordered_shots():
    _, episode, storyboard = make_episode(with_character=False)
    shots = sync_storyboard_shots(storyboard)
    track = SubtitleTrack.objects.create(episode=episode)
    for shot in shots:
        ShotSubtitleSetting.objects.create(
            shot=shot,
            status=ShotSubtitleSetting.STATUS_CONFIRMED,
        )
    track.cues.create(
        shot=shots[0],
        position=1,
        source_text="first",
        text="first",
        start_ms=100,
        end_ms=900,
        local_start_ms=100,
        local_end_ms=900,
        needs_review=False,
    )
    track.cues.create(
        shot=shots[1],
        position=2,
        source_text="second",
        text="second",
        start_ms=200,
        end_ms=1000,
        local_start_ms=200,
        local_end_ms=1000,
        needs_review=False,
    )

    before = subtitle_snapshot(track, assets=[None, None])
    reorder_shots(episode, [str(shots[1].shot_id), str(shots[0].shot_id)])
    after = subtitle_snapshot(track, assets=[None, None])

    assert [cue["text"] for cue in before["cues"]] == ["first", "second"]
    assert [cue["text"] for cue in after["cues"]] == ["second", "first"]
    assert after["cues"][0]["start_ms"] == 200
    assert after["cues"][1]["start_ms"] == shots[1].duration_seconds * 1000 + 100


def test_recognize_speech_alignment_uses_word_timestamps(monkeypatch, tmp_path):
    from studio.services import subtitles

    words = [
        SimpleNamespace(word="快", start=0.20, end=0.30),
        SimpleNamespace(word="走", start=0.31, end=0.42),
        SimpleNamespace(word="等", start=1.00, end=1.10),
        SimpleNamespace(word="等", start=1.11, end=1.24),
    ]
    segment = SimpleNamespace(text="快走等等", start=0.20, end=1.24, words=words)
    fake_model = SimpleNamespace(
        transcribe=lambda *args, **kwargs: ([segment], SimpleNamespace())
    )
    monkeypatch.setenv("SUBTITLE_ASR_BACKEND", "faster_whisper")
    monkeypatch.setattr(subtitles, "_WHISPER_MODEL", fake_model)
    video_path = tmp_path / "speech.mp4"
    video_path.write_bytes(b"video")
    asset = SimpleNamespace(video=SimpleNamespace(path=str(video_path)))

    alignment = recognize_speech_alignment(asset, "快走。等等。", ["快走", "等等"])

    assert alignment[0] == [(200, 420), (1000, 1240)]
    assert alignment[1] == "快走等等"
    assert alignment[2] == 1.0


def test_recognize_speech_alignment_hides_unmatched_parts(monkeypatch, tmp_path):
    from studio.services import subtitles

    words = [SimpleNamespace(word="secondline", start=1.0, end=2.0)]
    segment = SimpleNamespace(text="second line", start=1.0, end=2.0, words=words)
    fake_model = SimpleNamespace(
        transcribe=lambda *args, **kwargs: ([segment], SimpleNamespace())
    )
    monkeypatch.setenv("SUBTITLE_ASR_BACKEND", "faster_whisper")
    monkeypatch.setattr(subtitles, "_WHISPER_MODEL", fake_model)
    video_path = tmp_path / "partial-speech.mp4"
    video_path.write_bytes(b"video")
    asset = SimpleNamespace(video=SimpleNamespace(path=str(video_path)))

    alignment = recognize_speech_alignment(
        asset,
        "missing second line",
        ["missing", "second line"],
    )

    assert alignment[0] == [None, (1000, 2000)]


def test_recognize_speech_alignment_splits_one_word_without_overlap(
    monkeypatch,
    tmp_path,
):
    from studio.services import subtitles

    words = [SimpleNamespace(word="abcd", start=0.2, end=1.2)]
    segment = SimpleNamespace(text="abcd", start=0.2, end=1.2, words=words)
    fake_model = SimpleNamespace(
        transcribe=lambda *args, **kwargs: ([segment], SimpleNamespace())
    )
    monkeypatch.setenv("SUBTITLE_ASR_BACKEND", "faster_whisper")
    monkeypatch.setattr(subtitles, "_WHISPER_MODEL", fake_model)
    video_path = tmp_path / "single-word.mp4"
    video_path.write_bytes(b"video")
    asset = SimpleNamespace(video=SimpleNamespace(path=str(video_path)))

    alignment = recognize_speech_alignment(asset, "abcd", ["ab", "cd"])

    assert alignment[0] == [(200, 700), (700, 1200)]


def test_split_dialogue_drops_punctuation_only_tail():
    assert split_dialogue(f"Speaker: {'a' * 18}.") == ["a" * 18]


def test_generate_subtitles_hides_unmatched_cues_and_updates_progress(monkeypatch):
    project, episode, storyboard = make_episode(with_character=False)
    shot = sync_storyboard_shots(storyboard)[0]
    shot.dialogue_or_narration = "Speaker: missing. Speaker: second line."
    shot.save(update_fields=["dialogue_or_narration", "updated_at"])
    asset = VideoAsset.objects.create(
        shot=shot,
        version=1,
        status=VideoAsset.STATUS_READY,
        prompt_snapshot="prompt",
        is_selected=True,
    )
    asset.video.save("partial-subtitle.mp4", ContentFile(b"not-a-real-video"))
    track = SubtitleTrack.objects.create(episode=episode)
    task = GenerationTask.objects.create(
        project=project,
        task_type=GenerationTask.TYPE_SUBTITLE_ALIGN,
        target_id=str(track.id),
    )
    monkeypatch.setattr(
        "studio.services.subtitles.recognize_speech_alignment",
        lambda *args, **kwargs: ([None, (1000, 2000)], "second line", 0.8),
    )

    generate_subtitle_cues(track, [asset], task_id=task.id)

    cues = list(track.cues.order_by("position"))
    task.refresh_from_db()
    assert [cue.text for cue in cues] == ["", "second line."]
    assert cues[0].needs_review is True
    assert track.qc_reason == "rule_fail:unmatched_speech"
    assert cues[1].local_start_ms == 1000
    assert cues[1].local_end_ms == 2000
    assert (task.progress_current, task.progress_total, task.progress_percent) == (
        1,
        1,
        100,
    )

def test_retime_subtitle_snapshot_uses_normalized_clip_durations():
    snapshot = {
        "style": {},
        "global_offset_ms": 0,
        "shots": [
            {"shot_id": "shot-1", "duration_ms": 5000},
            {"shot_id": "shot-2", "duration_ms": 5000},
        ],
        "cues": [
            {
                "position": 1,
                "shot_id": "shot-2",
                "text": "第二镜头",
                "start_ms": 5100,
                "end_ms": 5900,
                "local_start_ms": 100,
                "local_end_ms": 900,
                "shot_offset_ms": 0,
                "style": {},
            }
        ],
    }

    retimed = retime_subtitle_snapshot(
        snapshot,
        [5200, 4800],
        max_timeline_drift_ms=500,
    )

    assert retimed["cues"][0]["start_ms"] == 5300
    assert retimed["cues"][0]["end_ms"] == 6100
    assert retimed["timeline"]["normalized_duration_ms"] == 10000
    assert retimed["timeline"]["max_drift_ms"] == 200


def test_retime_subtitle_snapshot_rejects_excessive_timeline_drift():
    snapshot = {
        "style": {},
        "shots": [
            {"shot_id": "shot-1", "duration_ms": 5000},
            {"shot_id": "shot-2", "duration_ms": 5000},
        ],
        "cues": [],
    }

    with pytest.raises(ValueError, match="累计偏差 1000ms"):
        retime_subtitle_snapshot(
            snapshot,
            [6000, 4000],
            max_timeline_drift_ms=500,
        )


def test_retime_subtitle_snapshot_rejects_cue_outside_shot(monkeypatch):
    monkeypatch.setenv("SUBTITLE_CUE_BOUNDARY_TOLERANCE_MS", "300")
    snapshot = {
        "style": {},
        "shots": [{"shot_id": "shot-1", "duration_ms": 5000}],
        "cues": [
            {
                "position": 1,
                "shot_id": "shot-1",
                "text": "late caption",
                "start_ms": 4800,
                "end_ms": 5500,
                "local_start_ms": 4800,
                "local_end_ms": 5500,
            }
        ],
    }

    with pytest.raises(ValueError, match="超出所属镜头 500ms"):
        retime_subtitle_snapshot(snapshot, [5000])


def test_shot_subtitle_setting_uses_track_style_and_controls_visibility():
    _, episode, storyboard = make_episode(with_character=False)
    shots = sync_storyboard_shots(storyboard)
    track = SubtitleTrack.objects.create(
        episode=episode,
        style_options={"font_size": 52, "text_color": "#FDE68A"},
    )
    first_setting = ShotSubtitleSetting.objects.create(
        shot=shots[0],
        status=ShotSubtitleSetting.STATUS_CONFIRMED,
    )
    ShotSubtitleSetting.objects.create(
        shot=shots[1],
        enabled=False,
        status=ShotSubtitleSetting.STATUS_CONFIRMED,
    )
    first = track.cues.create(
        shot=shots[0],
        position=1,
        source_text="visible",
        text="visible",
        start_ms=0,
        end_ms=1000,
        local_start_ms=0,
        local_end_ms=1000,
    )
    track.cues.create(
        shot=shots[1],
        position=2,
        source_text="hidden",
        text="hidden",
        start_ms=0,
        end_ms=1000,
        local_start_ms=0,
        local_end_ms=1000,
    )

    save_shot_subtitle(
        track,
        first_setting,
        enabled=True,
        offset_ms=200,
        cues=[
            {
                "id": first.id,
                "text": "visible",
                "start_ms": 0,
                "end_ms": 1000,
                "reviewed": True,
            }
        ],
        confirm_all=True,
    )
    snapshot = subtitle_snapshot(track, assets=[None, None])
    ass = render_ass(snapshot)

    assert [cue["text"] for cue in snapshot["cues"]] == ["visible"]
    assert snapshot["cues"][0]["start_ms"] == 200
    assert snapshot["cues"][0]["style"]["font_size"] == 52
    assert "ShotStyle1" not in ass
    assert ",52," in ass

def test_split_dialogue_removes_speaker_and_limits_line_length():
    text = "\u6797\u9ed8\uff1a\u8fd9\u662f\u4e00\u53e5\u5f88\u957f\u5f88\u957f\u9700\u8981\u81ea\u52a8\u62c6\u5206\u7684\u4e2d\u6587\u5b57\u5e55\uff0c\u968f\u540e\u7ee7\u7eed\u524d\u8fdb\u3002"
    parts = split_dialogue(text)

    assert parts
    assert not parts[0].startswith("\u6797\u9ed8")
    assert all(len(part) <= 18 for part in parts)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "\u6797\u9ed8\uff1a\u201c\u522b\u56de\u5934\uff0c\u7ee7\u7eed\u8d70\u3002\u201d",
            ["\u522b\u56de\u5934\uff0c\u7ee7\u7eed\u8d70\u3002"],
        ),
        (
            "\u6797\u9ed8\uff08\u4f4e\u58f0\uff09\uff1a\u201c\u95e8\u5916\u6709\u4eba\u3002\u201d",
            ["\u95e8\u5916\u6709\u4eba\u3002"],
        ),
        (
            "\u3010\u6797\u9ed8\u3011\u201c\u5f00\u59cb\u5427\u201d",
            ["\u5f00\u59cb\u5427"],
        ),
        (
            "\u82cf\u6674:\"\u6797\u9ed8\uff0c\u4f60\u4e0d\u80fd\u53bb\uff01\"",
            ["\u6797\u9ed8\uff0c\u4f60\u4e0d\u80fd\u53bb\uff01"],
        ),
        (
            "\u6797\u9ed8\uff1a\u201c\u8d70\u3002\u201d \u82cf\u6674\uff1a\u201c\u7b49\u7b49\uff01\u201d",
            ["\u8d70\u3002", "\u7b49\u7b49\uff01"],
        ),
        (
            "\u65c1\u767d\uff1a\u96e8\u8d8a\u6765\u8d8a\u5927\u3002",
            [],
        ),
    ],
)
def test_split_dialogue_removes_each_speaker_and_quotes(source, expected):
    assert split_dialogue(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        "\u97f3\u6548\uff1a\u201c\u5494\u55d2\uff01\u201d\u65e0\u58f0\u8ba1\u65f6\uff1a\u201c\u4e00\u79d2\u3001\u4e24\u79d2\u3002\u201d",
        "\u7cfb\u7edf\u63d0\u793a\uff1a\u201c\u5ba1\u6838\u901a\u8fc7\u3002\u201d",
        "\u7ed3\u5c3e\u5b57\u5e55\uff1a\u201c\u4e3a\u4ec0\u4e48\u6ca1\u6709\u54cd\u5e94\uff1f\u201d",
        "\u65bd\u5de5\u58f0\u8fde\u7eed\u54cd\u8d77\u3002",
        "\u65e0\u5bf9\u767d",
    ],
)
def test_split_dialogue_ignores_non_spoken_content(source):
    assert split_dialogue(source, speaker_names=["\u79e6\u950b"]) == []


def test_split_dialogue_keeps_people_and_drops_effects_in_mixed_content():
    source = (
        "\u82cf\u665a\uff1a\u201c\u6211\u8fd8\u6709\u67f4\u6cb9\u53d1\u7535\u673a\u3002\u201d"
        "\u97f3\u6548\uff1a\u70df\u611f\u8b66\u62a5\u9aa4\u7136\u54cd\u8d77\u3002"
        "\u79e6\u950b\uff1a\u201c\u5148\u65ad\u7535\u3002\u201d"
        "\u8f6c\u573a\u5b57\u5e55\uff1a\u201c\u4e24\u5929\u524d\u3002\u201d"
    )

    assert split_dialogue(source, speaker_names=["\u82cf\u665a", "\u79e6\u950b"]) == [
        "\u6211\u8fd8\u6709\u67f4\u6cb9\u53d1\u7535\u673a\u3002",
        "\u5148\u65ad\u7535\u3002",
    ]

def test_split_dialogue_drops_unquoted_action_after_spoken_lines():
    source = (
        "\u5de5\u4eba\uff1a\u6700\u540e\u4e00\u6247\u95e8\u5b89\u88c5\u5b8c\u6210\u3002"
        "\u82cf\u665a\uff1a\u6307\u7eb9\u6b63\u5e38\u3002"
        "\u697c\u68af\u95f4\u4f20\u6765\u79e6\u950b\u9010\u7ea7\u9760\u8fd1\u7684\u811a\u6b65\u58f0\u3002"
    )

    assert split_dialogue(source, speaker_names=["\u5de5\u4eba", "\u82cf\u665a", "\u79e6\u950b"]) == [
        "\u6700\u540e\u4e00\u6247\u95e8\u5b89\u88c5\u5b8c\u6210\u3002",
        "\u6307\u7eb9\u6b63\u5e38\u3002",
    ]

def test_subtitle_generation_accepts_shot_without_spoken_dialogue():
    _, episode, storyboard = make_episode(with_character=False)
    shot = sync_storyboard_shots(storyboard)[0]
    shot.dialogue_or_narration = "\u97f3\u6548\uff1a\u811a\u6b65\u58f0\u8d8a\u6765\u8d8a\u8fd1\u3002"
    shot.character_names = []
    shot.save(update_fields=["dialogue_or_narration", "character_names", "updated_at"])
    asset = VideoAsset.objects.create(
        shot=shot,
        version=1,
        status=VideoAsset.STATUS_READY,
        prompt_snapshot="prompt",
        is_selected=True,
    )
    asset.video.save("silent-subtitle.mp4", ContentFile(b"video"))
    track = SubtitleTrack.objects.create(episode=episode)

    generate_subtitle_cues(track, [asset])
    track.refresh_from_db()

    assert not track.cues.exists()
    assert track.status == SubtitleTrack.STATUS_DRAFT
    assert track.qc_status == SubtitleTrack.QC_PASSED
    assert track.qc_reason == "rule_pass:no_spoken_dialogue"

def test_subtitle_regeneration_preserves_manually_edited_display_text():
    _, episode, storyboard = make_episode(with_character=False)
    shot = sync_storyboard_shots(storyboard)[0]
    asset = VideoAsset.objects.create(
        shot=shot,
        version=1,
        status=VideoAsset.STATUS_READY,
        prompt_snapshot="prompt",
        is_selected=True,
    )
    asset.video.save("manual-subtitle.mp4", ContentFile(b"video"))
    track = SubtitleTrack.objects.create(episode=episode)

    generate_subtitle_cues(track, [asset])
    cue = track.cues.get()
    cue.text = "\u4fdd\u7559\u201c\u539f\u6837\u201d"
    cue.is_manually_edited = True
    cue.needs_review = False
    cue.save(update_fields=["text", "is_manually_edited", "needs_review", "updated_at"])

    generate_subtitle_cues(track, [asset])

    regenerated = track.cues.get()
    assert regenerated.text == "\u4fdd\u7559\u201c\u539f\u6837\u201d"
    assert regenerated.is_manually_edited is True

def test_queue_export_snapshots_enabled_subtitles():
    _, episode, storyboard = make_episode(with_character=False)
    shots = sync_storyboard_shots(storyboard)
    for index, shot in enumerate(shots, start=1):
        asset = VideoAsset.objects.create(
            shot=shot,
            version=1,
            status=VideoAsset.STATUS_READY,
            prompt_snapshot="prompt",
            is_selected=True,
        )
        asset.video.save(f"queue-subtitle-{index}.mp4", ContentFile(b"video"))

    track = SubtitleTrack.objects.create(
        episode=episode,
        enabled=True,
        status=SubtitleTrack.STATUS_NEEDS_REVIEW,
    )
    track.cues.create(
        shot=shots[0],
        position=1,
        source_text="caption",
        text="caption",
        start_ms=200,
        end_ms=1200,
    )
    track.source_hash = subtitle_source_hash(episode)
    track.save(update_fields=["source_hash"])

    task, created = queue_export(episode)
    composition = VideoComposition.objects.get(pk=task.target_id)

    assert created is True
    assert composition.include_subtitles is True
    assert composition.subtitle_snapshot["cues"][0]["text"] == "caption"
    assert task.input_snapshot["include_subtitles"] is True


def test_queue_export_treats_all_disabled_shot_subtitles_as_no_subtitles():
    _, episode, storyboard = make_episode(with_character=False)
    shots = sync_storyboard_shots(storyboard)
    for index, shot in enumerate(shots, start=1):
        asset = VideoAsset.objects.create(
            shot=shot,
            version=1,
            status=VideoAsset.STATUS_READY,
            prompt_snapshot="prompt",
            is_selected=True,
        )
        asset.video.save(f"disabled-caption-{index}.mp4", ContentFile(b"video"))
        ShotSubtitleSetting.objects.create(
            shot=shot,
            enabled=False,
            status=ShotSubtitleSetting.STATUS_CONFIRMED,
        )

    track = SubtitleTrack.objects.create(
        episode=episode,
        enabled=True,
        status=SubtitleTrack.STATUS_CONFIRMED,
    )
    track.cues.create(
        shot=shots[0],
        position=1,
        source_text="disabled",
        text="disabled",
        start_ms=0,
        end_ms=1000,
        local_start_ms=0,
        local_end_ms=1000,
    )

    task, _ = queue_export(episode)
    composition = VideoComposition.objects.get(pk=task.target_id)

    assert composition.include_subtitles is False
    assert composition.variant == VideoComposition.VARIANT_CLEAN
    assert composition.subtitle_snapshot == {}
    assert task.input_snapshot["include_subtitles"] is False

def test_ffmpeg_export_burns_ass_when_subtitles_are_enabled(tmp_path, monkeypatch):
    source = tmp_path / "shot.mp4"
    source.write_bytes(b"source")
    asset = SimpleNamespace(
        video=SimpleNamespace(path=str(source)),
        shot=SimpleNamespace(duration_seconds=6),
    )
    commands = []

    monkeypatch.setattr("studio.services.video.shutil.which", lambda name: name)

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == "ffprobe":
            return SimpleNamespace(stdout="0\n")
        Path(command[-1]).write_bytes(b"exported-video")
        return SimpleNamespace(stdout=b"", stderr=b"")

    monkeypatch.setattr("studio.services.video.subprocess.run", fake_run)
    snapshot = {
        "style": {},
        "cues": [{"position": 1, "text": "caption", "start_ms": 0, "end_ms": 1000}],
    }

    content = _ffmpeg_concat([asset], subtitle_snapshot=snapshot)

    burn_command = next(command for command in commands if "ass=subtitles.ass" in command)
    assert content == b"exported-video"
    assert burn_command[burn_command.index("-c:a") + 1] == "copy"


def test_ffmpeg_export_retimes_subtitles_from_normalized_clips(tmp_path, monkeypatch):
    assets = []
    for index in range(1, 3):
        source = tmp_path / f"shot-{index}.mp4"
        source.write_bytes(b"source")
        assets.append(
            SimpleNamespace(
                video=SimpleNamespace(path=str(source)),
                shot=SimpleNamespace(shot_id=f"shot-{index}", duration_seconds=5),
            )
        )

    monkeypatch.setattr("studio.services.video.shutil.which", lambda name: name)

    def fake_run(command, **kwargs):
        if command[0] == "ffprobe":
            if "stream=index" in command:
                return SimpleNamespace(stdout="")
            duration = "5.2\n" if "normalized-001" in command[-1] else "4.8\n"
            return SimpleNamespace(stdout=duration)
        Path(command[-1]).write_bytes(b"exported-video")
        return SimpleNamespace(stdout=b"", stderr=b"")

    monkeypatch.setattr("studio.services.video.subprocess.run", fake_run)
    snapshot = {
        "style": {},
        "global_offset_ms": 0,
        "shots": [
            {"shot_id": "shot-1", "duration_ms": 5000},
            {"shot_id": "shot-2", "duration_ms": 5000},
        ],
        "cues": [
            {
                "position": 1,
                "shot_id": "shot-2",
                "text": "caption",
                "start_ms": 5100,
                "end_ms": 5900,
                "local_start_ms": 100,
                "local_end_ms": 900,
                "shot_offset_ms": 0,
                "style": {},
            }
        ],
    }

    content, effective = _ffmpeg_concat(
        assets,
        subtitle_snapshot=snapshot,
        return_subtitle_snapshot=True,
    )

    assert content == b"exported-video"
    assert effective["cues"][0]["start_ms"] == 5300
    assert effective["timeline"]["max_drift_ms"] == 200


def test_cold_open_subtitles_are_cropped_then_body_cues_shift_by_three_seconds():
    snapshot = {
        "style": {},
        "global_offset_ms": 0,
        "shots": [
            {"shot_id": "shot-1", "duration_ms": 6000},
            {"shot_id": "shot-2", "duration_ms": 4000},
        ],
        "cues": [
            {
                "position": 1,
                "shot_id": "shot-2",
                "text": "反转台词",
                "start_ms": 6500,
                "end_ms": 8500,
                "local_start_ms": 500,
                "local_end_ms": 2500,
                "shot_offset_ms": 0,
                "style": {},
            }
        ],
    }
    edit_plan = [
        {
            "segment_id": "cold-open:shot-2",
            "asset_id": 2,
            "shot_id": "shot-2",
            "role": "cold_open",
            "in_ms": 0,
            "out_ms": 3000,
        },
        {
            "segment_id": "body:shot-1",
            "asset_id": 1,
            "shot_id": "shot-1",
            "role": "body",
            "in_ms": 0,
            "out_ms": None,
        },
        {
            "segment_id": "body:shot-2",
            "asset_id": 2,
            "shot_id": "shot-2",
            "role": "body",
            "in_ms": 0,
            "out_ms": None,
        },
    ]

    effective = retime_subtitle_snapshot_for_edit_plan(
        snapshot,
        edit_plan,
        [3000, 6000, 4000],
    )

    assert [(cue["timeline_role"], cue["start_ms"], cue["end_ms"]) for cue in effective["cues"]] == [
        ("cold_open", 500, 2500),
        ("body", 9500, 11500),
    ]
    assert effective["timeline"]["normalized_duration_ms"] == 13000
    assert effective["timeline"]["cold_open_duration_ms"] == 3000


def test_ffmpeg_edit_plan_trims_cold_open_before_reusing_full_asset(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    asset = SimpleNamespace(
        id=7,
        video=SimpleNamespace(path=str(source)),
        shot=SimpleNamespace(shot_id="shot-7", duration_seconds=6),
    )
    commands = []
    monkeypatch.setattr("studio.services.video.shutil.which", lambda name: name)

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == "ffprobe":
            return SimpleNamespace(stdout="0\n")
        Path(command[-1]).write_bytes(b"exported-video")
        return SimpleNamespace(stdout=b"", stderr=b"")

    monkeypatch.setattr("studio.services.video.subprocess.run", fake_run)
    edit_plan = [
        {
            "asset_id": 7,
            "shot_id": "shot-7",
            "role": "cold_open",
            "in_ms": 500,
            "out_ms": 3500,
        },
        {
            "asset_id": 7,
            "shot_id": "shot-7",
            "role": "body",
            "in_ms": 0,
            "out_ms": None,
        },
    ]

    content = _ffmpeg_concat([asset], edit_plan=edit_plan)

    normalize_commands = [command for command in commands if "-vf" in command]
    assert content == b"exported-video"
    assert len(normalize_commands) == 2
    assert normalize_commands[0][normalize_commands[0].index("-ss") + 1] == "0.500"
    assert normalize_commands[0][normalize_commands[0].index("-t") + 1] == "3.000"
    assert "-ss" not in normalize_commands[1]
    assert "-t" not in normalize_commands[1]


def test_subtitle_generate_save_and_download_views(client):
    project, episode, storyboard = make_episode(with_character=False)
    shots = sync_storyboard_shots(storyboard)
    for index, shot in enumerate(shots, start=1):
        asset = VideoAsset.objects.create(
            shot=shot,
            version=1,
            status=VideoAsset.STATUS_READY,
            prompt_snapshot="prompt",
            is_selected=True,
        )
        asset.video.save(f"view-subtitle-{index}.mp4", ContentFile(b"video"))

    generate_response = client.post(
        reverse("studio:generate_subtitles", args=[project.workspace_id, 1])
    )

    assert generate_response.status_code == 302
    assert "subtitle=1" in generate_response.url
    track = SubtitleTrack.objects.get(episode=episode)
    assert track.status == SubtitleTrack.STATUS_ALIGNING
    task = GenerationTask.objects.get(task_type=GenerationTask.TYPE_SUBTITLE_ALIGN)
    process_subtitle_task(task.id)
    track.refresh_from_db()
    cues = list(track.cues.order_by("position"))
    assert cues
    save_subtitle_style(track, {"font_size": 47, "text_color": "#AABBCC"})

    payload = {
        "enabled": "1",
        "global_offset_ms": "100",
        "font_name": "Noto Sans CJK SC",
        "font_size": "38",
        "text_color": "#FFFFFF",
        "outline_color": "#000000",
        "outline_size": "3",
        "margin_bottom": "92",
        "action": "confirm_all",
        "cue_id": [str(cue.id) for cue in cues],
    }
    for cue in cues:
        payload[f"cue_text_{cue.id}"] = cue.text
        payload[f"cue_start_{cue.id}"] = f"{cue.start_ms / 1000:.2f}"
        payload[f"cue_end_{cue.id}"] = f"{cue.end_ms / 1000:.2f}"

    save_response = client.post(
        reverse("studio:save_subtitles", args=[project.workspace_id, 1]),
        payload,
    )

    assert save_response.status_code == 302
    track.refresh_from_db()
    assert track.status == SubtitleTrack.STATUS_CONFIRMED
    assert track.global_offset_ms == 100
    assert track.style_options["font_size"] == 47
    assert track.style_options["text_color"] == "#AABBCC"

    download_response = client.get(
        reverse("studio:download_subtitles", args=[project.workspace_id, 1])
    )
    assert download_response.status_code == 200
    assert "application/x-subrip" in download_response["Content-Type"]
    assert "-->" in download_response.content.decode("utf-8")

def test_subtitle_style_view_is_shared_across_video_tabs(client):
    project, episode, storyboard = make_episode(with_character=False)
    sync_storyboard_shots(storyboard)

    response = client.post(
        reverse("studio:save_subtitle_style", args=[project.workspace_id, 1]),
        {
            "return_tab": "shots",
            "font_name": "Microsoft YaHei",
            "font_size": "49",
            "text_color": "#abcdef",
            "outline_color": "#123456",
            "outline_size": "4",
            "margin_bottom": "120",
        },
    )

    assert response.status_code == 302
    assert "tab=shots" in response.url
    track = SubtitleTrack.objects.get(episode=episode)
    assert track.style_options == {
        "font_name": "Microsoft YaHei",
        "font_size": 49,
        "text_color": "#ABCDEF",
        "outline_color": "#123456",
        "outline_size": 4,
        "margin_bottom": 120,
    }

    shots_page = client.get(
        reverse("studio:video_episode", args=[project.workspace_id, 1]),
        {"tab": "shots"},
    ).content.decode("utf-8")
    assembly_page = client.get(
        reverse("studio:video_episode", args=[project.workspace_id, 1]),
        {"tab": "assembly"},
    ).content.decode("utf-8")

    assert 'id="subtitle-style-editor"' in shots_page
    assert 'data-modal-target="subtitle-style-editor"' in shots_page
    assert 'id="subtitle-style-editor"' in assembly_page
    assert 'data-modal-target="subtitle-style-editor"' in assembly_page


def test_shot_subtitle_generate_and_save_views(client):
    project, episode, storyboard = make_episode(with_character=False)
    shots = sync_storyboard_shots(storyboard)
    for index, shot in enumerate(shots, start=1):
        asset = VideoAsset.objects.create(
            shot=shot,
            version=1,
            status=VideoAsset.STATUS_READY,
            prompt_snapshot="prompt",
            is_selected=True,
        )
        asset.video.save(f"shot-caption-{index}.mp4", ContentFile(b"video"))

    generate_response = client.post(
        reverse(
            "studio:generate_shot_subtitles",
            args=[project.workspace_id, 1, shots[0].shot_id],
        ),
        {"return_tab": "shots"},
    )

    assert generate_response.status_code == 302
    assert f"shot_subtitle={shots[0].shot_id}" in generate_response.url
    task = GenerationTask.objects.get(task_type=GenerationTask.TYPE_SUBTITLE_ALIGN)
    assert task.input_snapshot["shot_ids"] == [shots[0].id]
    process_subtitle_task(task.id)

    track = SubtitleTrack.objects.get(episode=episode)
    cue = track.cues.get(shot=shots[0])
    assert not track.cues.filter(shot=shots[1]).exists()

    save_response = client.post(
        reverse(
            "studio:save_shot_subtitles",
            args=[project.workspace_id, 1, shots[0].shot_id],
        ),
        {
            "return_tab": "shots",
            "enabled": "1",
            "offset_ms": "150",
            "font_name": "Noto Sans CJK SC",
            "font_size": "48",
            "text_color": "#FFFFFF",
            "outline_color": "#000000",
            "outline_size": "3",
            "margin_bottom": "110",
            "action": "confirm_all",
            "cue_id": [str(cue.id)],
            f"cue_text_{cue.id}": cue.text,
            f"cue_start_{cue.id}": f"{cue.local_start_ms / 1000:.2f}",
            f"cue_end_{cue.id}": f"{cue.local_end_ms / 1000:.2f}",
        },
    )

    assert save_response.status_code == 302
    setting = ShotSubtitleSetting.objects.get(shot=shots[0])
    assert setting.status == ShotSubtitleSetting.STATUS_CONFIRMED
    assert setting.offset_ms == 150
    assert not hasattr(setting, "style_options")

    page = client.get(
        reverse("studio:video_episode", args=[project.workspace_id, 1])
    )
    assert page.status_code == 200
    assert f'id="shot-subtitle-{shots[0].id}"'.encode() in page.content


def test_storyboard_character_candidates_create_image_tasks():
    from studio.services.characters import (
        create_storyboard_characters,
        storyboard_character_candidates,
    )

    _, episode, storyboard = make_episode(with_character=False)
    storyboard.prompts_payload[0]["character_names"] = ["New Hero"]
    storyboard.save(update_fields=["prompts_payload", "updated_at"])
    sync_storyboard_shots(storyboard)

    candidates = storyboard_character_candidates(episode.script)
    assert [(item["name"], item["shot_count"]) for item in candidates] == [
        ("New Hero", 1)
    ]

    tasks = create_storyboard_characters(episode.script, ["New Hero"])

    character = Character.objects.get(script=episode.script, name="New Hero")
    assert character.role == "Storyboard character"
    assert tasks[0]["target_id"] == str(character.id)
    assert GenerationTask.objects.filter(
        task_type=GenerationTask.TYPE_CHARACTER_IMAGE,
        target_id=str(character.id),
    ).exists()


def test_saved_storyboard_character_asset_binds_all_named_shots(settings, tmp_path):
    from studio.repositories.workspace import WorkspaceRepository
    from studio.services.characters import create_storyboard_characters

    settings.MEDIA_ROOT = tmp_path
    project, episode, storyboard = make_episode(with_character=False)
    for payload in storyboard.prompts_payload:
        payload["character_names"] = ["New Hero"]
    storyboard.save(update_fields=["prompts_payload", "updated_at"])
    shots = sync_storyboard_shots(storyboard)
    create_storyboard_characters(episode.script, ["New Hero"])
    character = Character.objects.get(script=episode.script, name="New Hero")

    WorkspaceRepository().save_character_asset(
        project.workspace_id,
        character.id,
        SimpleNamespace(
            model="image-model",
            source_url="",
            extension=".png",
            content=b"fake-image",
        ),
    )

    assert ShotCharacterReference.objects.filter(
        shot__in=shots,
        character=character,
    ).count() == 2


def test_bind_shot_characters_rejects_more_than_five():
    from studio.services.video import bind_shot_characters

    _, episode, storyboard = make_episode(with_character=False)
    shot = sync_storyboard_shots(storyboard)[0]
    character_ids = []
    for index in range(6):
        character = Character.objects.create(
            script=episode.script,
            name=f"Character {index}",
            role="Role",
            appearance="Appearance",
            image_prompt="Prompt",
            position=index + 1,
        )
        character_ids.append(str(character.id))

    with pytest.raises(ValueError, match="up to 5"):
        bind_shot_characters(shot, character_ids)


def test_delete_character_view_soft_deletes_and_unbinds(client):
    project, episode, storyboard = make_episode()
    shot = sync_storyboard_shots(storyboard)[0]
    reference = ShotCharacterReference.objects.get(shot=shot)
    character = reference.character

    response = client.post(
        reverse("studio:delete_character", args=[project.workspace_id, character.id]),
        {"script_id": episode.script_id},
    )

    assert response.status_code == 302
    assert response["Location"] == (
        f'{reverse("studio:project_workbench", args=[episode.script.outline_id])}'
        "?view=characters"
    )
    character.refresh_from_db()
    assert character.is_deleted is True
    assert character.assets.exists()
    assert not ShotCharacterReference.objects.filter(character=character).exists()


def test_video_page_lists_unknown_storyboard_characters(client):
    project, _, storyboard = make_episode(with_character=False)
    storyboard.prompts_payload[0]["character_names"] = ["New Hero"]
    storyboard.save(update_fields=["prompts_payload", "updated_at"])
    sync_storyboard_shots(storyboard)

    response = client.get(
        reverse("studio:video_episode", args=[project.workspace_id, 1])
    )

    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert 'data-modal-target="discover-storyboard-characters"' in content
    assert 'value="New Hero"' in content
    assert "New Hero" in content


def test_manual_subtitle_save_reruns_qc():
    _, episode, storyboard = make_episode(with_character=False)
    shot = sync_storyboard_shots(storyboard)[0]
    track = SubtitleTrack.objects.create(episode=episode)
    cue = track.cues.create(
        shot=shot,
        position=1,
        source_text="Hello",
        text="Hello",
        start_ms=0,
        end_ms=1500,
        local_start_ms=0,
        local_end_ms=1500,
    )

    save_subtitle_track(
        track,
        enabled=True,
        global_offset_ms=0,
        cues=[
            {
                "id": cue.id,
                "text": "Hello",
                "start_ms": 0,
                "end_ms": 1500,
                "reviewed": True,
            }
        ],
        confirm_all=True,
    )
    track.refresh_from_db()
    passed_hash = track.qc_content_hash
    assert track.qc_status == SubtitleTrack.QC_PASSED
    assert track.status == SubtitleTrack.STATUS_CONFIRMED

    save_subtitle_track(
        track,
        enabled=True,
        global_offset_ms=0,
        cues=[
            {
                "id": cue.id,
                "text": "Click",
                "start_ms": 0,
                "end_ms": 1500,
                "reviewed": True,
            }
        ],
        confirm_all=True,
    )
    track.refresh_from_db()

    assert track.qc_status == SubtitleTrack.QC_FAILED
    assert track.qc_reason == "rule_fail:hallucination_meta"
    assert track.qc_content_hash != passed_hash
    assert track.status == SubtitleTrack.STATUS_CONFIRMED
