import base64
from pathlib import Path
from types import SimpleNamespace

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
    render_ass,
    render_srt,
    save_shot_subtitle,
    save_subtitle_style,
    save_subtitle_track,
    split_dialogue,
    subtitle_snapshot,
    subtitle_source_hash,
)
from studio.services.video import (
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
    assert len(cues) == 2
    assert all(cue.text for cue in cues)
    assert cues[0].start_ms < cues[0].end_ms <= 6000
    assert cues[1].start_ms >= 6000
    assert cues[0].local_start_ms < cues[0].local_end_ms <= 6000
    assert cues[1].local_start_ms < cues[1].local_end_ms <= 6000
    assert all(cue.needs_review for cue in cues)
    assert ShotSubtitleSetting.objects.filter(
        shot__in=shots,
        status=ShotSubtitleSetting.STATUS_NEEDS_REVIEW,
    ).count() == 2


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
