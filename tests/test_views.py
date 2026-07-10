import pytest
from django.http import HttpResponse
from django.urls import reverse

from studio.constants import GENRES
from studio.models import Outline, Project
from studio.repositories.workspace import WorkspaceRepository

pytestmark = pytest.mark.django_db


def outline_payload():
    return [
        {
            "id": f"outline-{index}",
            "title": f"Outline title {index}",
            "core_premise": f"Core premise {index}",
            "protagonist": f"Protagonist {index}",
            "hook": f"Hook {index}",
            "arc_summary": f"Arc summary {index}",
        }
        for index in range(1, 7)
    ]


def script_payload():
    return {
        "script_plan": [
            {
                "episode": index,
                "title": f"Episode {index} title",
                "summary": f"Episode {index} summary",
                "key_conflict": f"Episode {index} conflict",
                "cliffhanger": f"Episode {index} cliffhanger",
            }
            for index in range(1, 61)
        ],
        "episode_1_script": "Episode 1 complete script",
    }


def storyboard_payload():
    return [
        {
            "shot_number": index,
            "duration": f"{index + 2}s",
            "visual_description": f"Visual description {index}",
            "character_action": f"Character action {index}",
            "dialogue_or_narration": f"Dialogue {index}",
            "camera_language": f"Camera language {index}",
            "image_prompt": f"Image prompt {index}",
            "video_prompt": f"Video prompt {index}",
        }
        for index in range(1, 13)
    ]


def write_workspace(**overrides):
    repo = WorkspaceRepository()
    workspace_id = overrides.pop("id", "workspace-1")
    genre = overrides.pop("genre", GENRES[0])
    workspace = repo.create_workspace(genre, workspace_id=workspace_id)

    outlines = overrides.pop("outlines", None)
    selected_outline_id = overrides.pop("selected_outline_id", None)
    script_plan = overrides.pop("script_plan", None)
    episode_1_script = overrides.pop("episode_1_script", None)
    storyboard_prompts = overrides.pop("storyboard_prompts", None)

    needs_outline = selected_outline_id or script_plan is not None or episode_1_script is not None
    if outlines is None and needs_outline:
        outlines = outline_payload()
        selected_outline_id = selected_outline_id or outlines[0]["id"]
    if outlines is not None:
        workspace = repo.update_workspace(workspace_id, outlines=outlines)
    if selected_outline_id:
        workspace = repo.update_workspace(workspace_id, selected_outline_id=selected_outline_id)
    if script_plan is not None or episode_1_script is not None:
        workspace = repo.update_workspace(
            workspace_id,
            script_plan=script_plan if script_plan is not None else [],
            episode_1_script=episode_1_script if episode_1_script is not None else "",
        )
    if storyboard_prompts is not None:
        workspace = repo.update_workspace(workspace_id, storyboard_prompts=storyboard_prompts)

    return workspace


def read_workspace(workspace_id="workspace-1"):
    return WorkspaceRepository().get_workspace(workspace_id)


def test_outline_page_loads_current_outlines_from_database(client):
    WorkspaceRepository().replace_current_outline_set(GENRES[0], outline_payload())

    response = client.get(reverse("studio:outline"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Outline title 1" in content
    assert "Core premise 1" in content


def test_outline_page_allows_empty_database(client):
    response = client.get(reverse("studio:outline"))

    assert response.status_code == 200

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
    assert payload["episode_1_script"] == "Episode 1 complete script"


def test_storyboard_payload_returns_twelve_prompts():
    prompts = storyboard_payload()

    assert len(prompts) == 12


def test_generate_outlines_writes_workspace(client, monkeypatch):
    provider = object()
    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: provider)
    monkeypatch.setattr("studio.views.generate_outlines", lambda llm, genre: outline_payload())

    response = client.post(reverse("studio:generate_outlines"), {"genre": GENRES[0]})

    assert response.status_code == 200
    assert "Outline title 1" in response.content.decode("utf-8")

    workspace = WorkspaceRepository().get_current_workspace()
    assert Project.objects.count() == 1
    assert Outline.objects.count() == 6
    assert workspace["genre"] == GENRES[0]
    assert [outline["source_id"] for outline in workspace["outlines"]] == [outline["id"] for outline in outline_payload()]
    assert all(outline["is_usable"] is False for outline in workspace["outlines"])
    assert workspace["selected_outline_id"] is None


def test_generate_outlines_reuses_current_workspace(client, monkeypatch):
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
    workspace = WorkspaceRepository().get_current_workspace()
    assert Project.objects.count() == 1
    assert Outline.objects.count() == 6
    assert workspace["genre"] == GENRES[1]
    assert {outline["title"] for outline in workspace["outlines"]} == {"second"}
    assert workspace["selected_outline_id"] is None


def test_select_outline_redirects_to_script_page(client):
    workspace = write_workspace(outlines=outline_payload())

    response = client.post(
        reverse("studio:select_outline"),
        {"workspace_id": workspace["id"], "outline_id": "outline-2"},
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("studio:script", args=[workspace["id"]])

    saved = read_workspace(workspace["id"])
    assert saved["selected_outline_id"] == "outline-2"


def test_select_outline_handles_unknown_outline_id(client, monkeypatch):
    workspace = write_workspace(outlines=outline_payload())
    captured = {}

    def fake_render_outline(request, workspace=None, error=None, genre=None):
        captured["workspace"] = workspace
        captured["error"] = error
        return HttpResponse(error or "")

    monkeypatch.setattr("studio.views._render_outline", fake_render_outline)

    response = client.post(
        reverse("studio:select_outline"),
        {"workspace_id": workspace["id"], "outline_id": "missing-outline"},
    )

    assert response.status_code == 200
    assert captured["workspace"]["id"] == workspace["id"]
    assert captured["error"]
    assert response.content.decode("utf-8") == captured["error"]


def test_generate_script_get_returns_405_without_writing_workspace(client):
    workspace = write_workspace(
        outlines=outline_payload(),
        selected_outline_id="outline-1",
        script_plan=[{"episode": 1, "title": "old title"}],
        episode_1_script="old script",
    )
    before = read_workspace(workspace["id"])

    response = client.get(reverse("studio:generate_script", args=[before["id"]]))

    assert response.status_code == 405
    assert read_workspace(workspace["id"]) == before


def test_generate_script_starts_background_generation(client, monkeypatch):
    workspace = write_workspace(
        outlines=outline_payload(),
        selected_outline_id="outline-1",
    )
    started = []
    monkeypatch.setattr(
        "studio.views._start_background_script_generation",
        lambda workspace_id, outline_id: started.append((workspace_id, outline_id)),
    )

    response = client.post(reverse("studio:generate_script", args=[workspace["id"]]))

    assert response.status_code == 302
    assert response["Location"] == reverse("studio:script", args=[workspace["id"]])
    assert started == [(workspace["id"], "outline-1")]
    saved = read_workspace(workspace["id"])
    assert saved["selected_outline"]["script_status"] == "generating"
    assert saved["script_plan"] == []
    assert saved["episode_1_script"] == ""


def test_background_script_error_keeps_previous_content(monkeypatch):
    workspace = write_workspace(
        outlines=outline_payload(),
        selected_outline_id="outline-1",
        script_plan=[{"episode": 1, "title": "old title"}],
        episode_1_script="old script",
    )
    WorkspaceRepository().start_script_generation(workspace["id"], "outline-1")
    provider = object()
    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: provider)

    def raise_error(llm, outline):
        raise ValueError("script generation failed")

    monkeypatch.setattr("studio.views.generate_script", raise_error)

    from studio.views import _run_script_generation

    _run_script_generation(workspace["id"], "outline-1")

    saved = read_workspace(workspace["id"])
    assert saved["script_plan"] == [{"episode": 1, "title": "old title"}]
    assert saved["episode_1_script"] == "old script"
    assert saved["selected_outline"]["script_status"] == "failed"
    assert "script generation failed" in saved["selected_outline"]["script_error"]


def test_generate_storyboard_get_returns_405_without_writing_workspace(client):
    workspace = write_workspace(
        episode_1_script="old script",
        storyboard_prompts=[{"shot_number": 1, "image_prompt": "old prompt"}],
    )
    before = read_workspace(workspace["id"])

    response = client.get(reverse("studio:generate_storyboard", args=[before["id"]]))

    assert response.status_code == 405
    assert read_workspace(workspace["id"]) == before


def test_generate_storyboard_writes_prompts(client, monkeypatch):
    workspace = write_workspace(episode_1_script="Episode 1 complete script")
    provider = object()
    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: provider)
    monkeypatch.setattr(
        "studio.views.generate_storyboard",
        lambda llm, episode_1_script: storyboard_payload(),
    )

    response = client.post(reverse("studio:generate_storyboard", args=[workspace["id"]]))

    assert response.status_code == 200
    saved = read_workspace(workspace["id"])
    assert len(saved["storyboard_prompts"]) == 12


def test_generation_error_keeps_previous_content(client, monkeypatch):
    workspace = write_workspace(
        episode_1_script="keep old script",
        storyboard_prompts=storyboard_payload(),
    )
    provider = object()
    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: provider)
    monkeypatch.setattr(
        "studio.views._render_storyboard",
        lambda request, workspace, error=None: HttpResponse(error or ""),
    )

    def raise_error(llm, episode_1_script):
        raise ValueError("storyboard generation failed")

    monkeypatch.setattr("studio.views.generate_storyboard", raise_error)

    response = client.post(reverse("studio:generate_storyboard", args=[workspace["id"]]))

    assert response.status_code == 200
    assert "storyboard generation failed" in response.content.decode("utf-8")
    saved = read_workspace(workspace["id"])
    assert saved["episode_1_script"] == "keep old script"

def test_mark_outline_usable_keeps_outline_after_refresh(client, monkeypatch):
    provider = object()
    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: provider)
    monkeypatch.setattr("studio.views.generate_outlines", lambda llm, genre: outline_payload())

    client.post(reverse("studio:generate_outlines"), {"genre": GENRES[0]})
    workspace = WorkspaceRepository().get_current_workspace()
    first_outline_id = workspace["outlines"][0]["id"]

    response = client.post(
        reverse("studio:mark_outline_usable"),
        {"workspace_id": workspace["id"], "usable_outline_id": first_outline_id},
    )

    assert response.status_code == 302
    assert WorkspaceRepository().get_current_workspace()["usable_outlines"][0]["id"] == first_outline_id

    monkeypatch.setattr(
        "studio.views.generate_outlines",
        lambda llm, genre: [{**outline, "title": "New round"} for outline in outline_payload()],
    )
    client.post(reverse("studio:generate_outlines"), {"genre": GENRES[1]})
    refreshed = WorkspaceRepository().get_current_workspace()

    assert first_outline_id in [outline["id"] for outline in refreshed["usable_outlines"]]
    assert first_outline_id not in [outline["id"] for outline in refreshed["outlines"]]
    assert len(refreshed["outlines"]) == 6


def test_script_library_page_lists_usable_outlines(client):
    workspace = WorkspaceRepository().replace_current_outline_set(GENRES[0], outline_payload())
    first_outline_id = workspace["outlines"][0]["id"]
    WorkspaceRepository().mark_outline_usable(workspace["id"], first_outline_id)

    response = client.get(reverse("studio:script_index"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Outline title 1" in content
    assert "生成剧本" in content

def test_script_library_action_shows_view_script_when_script_exists(client):
    workspace = WorkspaceRepository().replace_current_outline_set(GENRES[0], outline_payload())
    first_outline_id = workspace["outlines"][0]["id"]
    repo = WorkspaceRepository()
    repo.mark_outline_usable(workspace["id"], first_outline_id)
    repo.update_workspace(workspace["id"], selected_outline_id=first_outline_id)
    repo.update_workspace(
        workspace["id"],
        script_plan=script_payload()["script_plan"],
        episode_1_script=script_payload()["episode_1_script"],
    )

    response = client.get(reverse("studio:script_index"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "查看剧本" in content
    assert "生成剧本" not in content


def test_selecting_unscripted_outline_does_not_show_previous_script(client):
    workspace = write_workspace(outlines=outline_payload(), selected_outline_id="outline-1")
    repo = WorkspaceRepository()
    repo.update_workspace(
        workspace["id"],
        script_plan=script_payload()["script_plan"],
        episode_1_script=script_payload()["episode_1_script"],
    )
    repo.update_workspace(workspace["id"], selected_outline_id="outline-2")

    selected = repo.get_workspace(workspace["id"])

    assert selected["selected_outline_id"] == "outline-2"
    assert selected["script_plan"] == []
    assert selected["episode_1_script"] == ""