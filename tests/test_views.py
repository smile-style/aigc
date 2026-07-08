import json

import pytest
from django.http import HttpResponse
from django.urls import reverse

from studio.constants import GENRES


def outline_payload():
    return [
        {
            "id": f"outline-{index}",
            "title": f"大纲标题 {index}",
            "core_premise": f"核心设定 {index}",
            "protagonist": f"主角 {index}",
            "hook": f"钩子 {index}",
            "arc_summary": f"长线梗概 {index}",
        }
        for index in range(1, 7)
    ]


def script_payload():
    return {
        "script_plan": [
            {
                "episode": index,
                "title": f"第{index}集标题",
                "summary": f"第{index}集摘要",
                "key_conflict": f"第{index}集冲突",
                "cliffhanger": f"第{index}集悬念",
            }
            for index in range(1, 61)
        ],
        "episode_1_script": "第1集完整剧本",
    }


def storyboard_payload():
    return [
        {
            "shot_number": index,
            "duration": f"{index + 2}秒",
            "visual_description": f"画面描述 {index}",
            "character_action": f"角色动作 {index}",
            "dialogue_or_narration": f"台词旁白 {index}",
            "camera_language": f"镜头语言 {index}",
            "image_prompt": f"图片提示词 {index}",
            "video_prompt": f"视频提示词 {index}",
        }
        for index in range(1, 13)
    ]


def write_workspace(tmp_path, **overrides):
    workspace = {
        "id": "workspace-1",
        "genre": GENRES[0],
        "episode_count": 60,
        "episode_duration_minutes": 2,
        "outlines": [],
        "selected_outline_id": None,
        "script_plan": [],
        "episode_1_script": "",
        "storyboard_prompts": [],
        "created_at": "2026-07-02T12:00:00+08:00",
        "updated_at": "2026-07-02T12:00:00+08:00",
    }
    workspace.update(overrides)
    path = tmp_path / f"{workspace['id']}.json"
    path.write_text(json.dumps(workspace, ensure_ascii=False, indent=2), encoding="utf-8")
    return workspace


def read_workspace(tmp_path, workspace_id="workspace-1"):
    return json.loads((tmp_path / f"{workspace_id}.json").read_text(encoding="utf-8"))


def test_outline_payload_returns_six_candidates():
    outlines = outline_payload()

    assert len(outlines) == 6
    assert set(outlines[0]) == {
        "id",
        "title",
        "core_premise",
        "protagonist",
        "hook",
        "arc_summary",
    }


def test_script_payload_returns_plan_and_episode_1_script():
    payload = script_payload()

    assert len(payload["script_plan"]) == 60
    assert payload["episode_1_script"] == "第1集完整剧本"


def test_storyboard_payload_returns_twelve_prompts():
    prompts = storyboard_payload()

    assert len(prompts) == 12


def test_generate_outlines_writes_workspace(client, settings, monkeypatch, tmp_path):
    settings.WORKSPACE_DIR = tmp_path
    provider = object()
    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: provider)
    monkeypatch.setattr("studio.views.generate_outlines", lambda llm, genre: outline_payload())

    response = client.post(reverse("studio:generate_outlines"), {"genre": GENRES[0]})

    assert response.status_code == 200
    assert "大纲标题 1" in response.content.decode("utf-8")

    workspace_files = list(tmp_path.glob("*.json"))
    assert len(workspace_files) == 1
    workspace = json.loads(workspace_files[0].read_text(encoding="utf-8"))
    assert workspace["genre"] == GENRES[0]
    assert workspace["outlines"] == outline_payload()
    assert workspace["selected_outline_id"] is None


def test_generate_outlines_reuses_current_workspace_file(client, settings, monkeypatch, tmp_path):
    settings.WORKSPACE_DIR = tmp_path
    provider = object()
    calls = []

    def fake_generate_outlines(llm, genre):
        calls.append(genre)
        prefix = "first" if len(calls) == 1 else "second"
        return [{**outline, "id": f"{prefix}-{outline['id']}", "title": prefix} for outline in outline_payload()]

    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: provider)
    monkeypatch.setattr("studio.views.generate_outlines", fake_generate_outlines)

    first_response = client.post(reverse("studio:generate_outlines"), {"genre": GENRES[0]})
    second_response = client.post(reverse("studio:generate_outlines"), {"genre": GENRES[1]})

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    workspace_files = sorted(path.name for path in tmp_path.glob("*.json"))
    assert workspace_files == ["current.json"]
    workspace = read_workspace(tmp_path, "current")
    assert workspace["genre"] == GENRES[1]
    assert len(workspace["outlines"]) == 6
    assert {outline["title"] for outline in workspace["outlines"]} == {"second"}
    assert workspace["selected_outline_id"] is None


def test_select_outline_redirects_to_script_page(client, settings, tmp_path):
    settings.WORKSPACE_DIR = tmp_path
    workspace = write_workspace(tmp_path, outlines=outline_payload())

    response = client.post(
        reverse("studio:select_outline"),
        {"workspace_id": workspace["id"], "outline_id": "outline-2"},
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("studio:script", args=[workspace["id"]])

    saved = read_workspace(tmp_path, workspace["id"])
    assert saved["selected_outline_id"] == "outline-2"


@pytest.mark.parametrize("outlines", [None, ["x"]])
def test_select_outline_handles_malformed_outlines(
    client, settings, monkeypatch, tmp_path, outlines
):
    settings.WORKSPACE_DIR = tmp_path
    workspace = write_workspace(tmp_path, outlines=outlines)
    captured = {}

    def fake_render_outline(request, workspace=None, error=None, genre=None):
        captured["workspace"] = workspace
        captured["error"] = error
        return HttpResponse(error or "")

    monkeypatch.setattr("studio.views._render_outline", fake_render_outline)

    response = client.post(
        reverse("studio:select_outline"),
        {"workspace_id": workspace["id"], "outline_id": "outline-1"},
    )

    assert response.status_code == 200
    assert captured["workspace"]["id"] == workspace["id"]
    assert captured["error"]
    assert response.content.decode("utf-8") == captured["error"]


def test_generate_script_get_returns_405_without_writing_workspace(client, settings, tmp_path):
    settings.WORKSPACE_DIR = tmp_path
    write_workspace(
        tmp_path,
        outlines=outline_payload(),
        selected_outline_id="outline-1",
        script_plan=[{"episode": 1, "title": "旧标题"}],
        episode_1_script="旧剧本",
    )
    before = read_workspace(tmp_path)

    response = client.get(reverse("studio:generate_script", args=[before["id"]]))

    assert response.status_code == 405
    assert read_workspace(tmp_path) == before


def test_generate_script_writes_plan_and_episode_script(
    client, settings, monkeypatch, tmp_path
):
    settings.WORKSPACE_DIR = tmp_path
    workspace = write_workspace(
        tmp_path,
        outlines=outline_payload(),
        selected_outline_id="outline-1",
    )
    provider = object()
    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: provider)
    monkeypatch.setattr(
        "studio.views.generate_script",
        lambda llm, outline: script_payload(),
    )

    response = client.post(reverse("studio:generate_script", args=[workspace["id"]]))

    assert response.status_code == 200
    saved = read_workspace(tmp_path, workspace["id"])
    assert len(saved["script_plan"]) == 60
    assert saved["episode_1_script"] == "第1集完整剧本"


def test_generate_script_error_keeps_previous_content(
    client, settings, monkeypatch, tmp_path
):
    settings.WORKSPACE_DIR = tmp_path
    workspace = write_workspace(
        tmp_path,
        outlines=outline_payload(),
        selected_outline_id="outline-1",
        script_plan=[{"episode": 1, "title": "旧标题"}],
        episode_1_script="旧剧本",
    )
    provider = object()
    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: provider)

    def raise_error(llm, outline):
        raise ValueError("剧本生成失败")

    monkeypatch.setattr("studio.views.generate_script", raise_error)
    monkeypatch.setattr(
        "studio.views._render_script",
        lambda request, workspace, error=None: HttpResponse(error or ""),
    )

    response = client.post(reverse("studio:generate_script", args=[workspace["id"]]))

    assert response.status_code == 200
    assert "剧本生成失败" in response.content.decode("utf-8")
    saved = read_workspace(tmp_path, workspace["id"])
    assert saved["script_plan"] == [{"episode": 1, "title": "旧标题"}]
    assert saved["episode_1_script"] == "旧剧本"


def test_generate_storyboard_get_returns_405_without_writing_workspace(
    client, settings, tmp_path
):
    settings.WORKSPACE_DIR = tmp_path
    write_workspace(
        tmp_path,
        episode_1_script="旧剧本",
        storyboard_prompts=[{"shot_number": 1, "image_prompt": "旧分镜"}],
    )
    before = read_workspace(tmp_path)

    response = client.get(reverse("studio:generate_storyboard", args=[before["id"]]))

    assert response.status_code == 405
    assert read_workspace(tmp_path) == before


def test_generate_storyboard_writes_prompts(client, settings, monkeypatch, tmp_path):
    settings.WORKSPACE_DIR = tmp_path
    workspace = write_workspace(tmp_path, episode_1_script="第1集完整剧本")
    provider = object()
    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: provider)
    monkeypatch.setattr(
        "studio.views.generate_storyboard",
        lambda llm, episode_1_script: storyboard_payload(),
    )

    response = client.post(reverse("studio:generate_storyboard", args=[workspace["id"]]))

    assert response.status_code == 200
    saved = read_workspace(tmp_path, workspace["id"])
    assert len(saved["storyboard_prompts"]) == 12


def test_generation_error_keeps_previous_content(client, settings, monkeypatch, tmp_path):
    settings.WORKSPACE_DIR = tmp_path
    workspace = write_workspace(
        tmp_path,
        episode_1_script="保留原有剧本",
        storyboard_prompts=storyboard_payload(),
    )
    provider = object()
    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: provider)
    monkeypatch.setattr(
        "studio.views._render_storyboard",
        lambda request, workspace, error=None: HttpResponse(error or ""),
    )

    def raise_error(llm, episode_1_script):
        raise ValueError("分镜生成失败")

    monkeypatch.setattr("studio.views.generate_storyboard", raise_error)

    response = client.post(reverse("studio:generate_storyboard", args=[workspace["id"]]))

    assert response.status_code == 200
    assert "分镜生成失败" in response.content.decode("utf-8")
    saved = read_workspace(tmp_path, workspace["id"])
    assert saved["episode_1_script"] == "保留原有剧本"
