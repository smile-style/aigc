import pytest
from django.http import HttpResponse
from django.urls import reverse

from studio.constants import GENRES
from studio.models import GenerationTask, Outline, Project
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


def character_profiles():
    return [
        {
            "name": "Chen Mo",
            "role": "Protagonist",
            "appearance": "Black hair and sharp features",
            "personality": "Calm and decisive",
            "costume": "Black coat",
            "image_prompt": "Full body character sheet on a plain background",
        }
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


def test_generate_storyboard_starts_background_task(client, monkeypatch):
    workspace = write_workspace(episode_1_script="Episode 1 complete script")
    started = []
    monkeypatch.setattr(
        "studio.views._start_background_storyboard_generation",
        lambda task_id: started.append(task_id),
    )

    response = client.post(reverse("studio:generate_storyboard", args=[workspace["id"]]))

    task = GenerationTask.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("studio:storyboard_episode", args=[workspace["id"], 1])
    assert started == [task.id]
    assert task.project.workspace_id == workspace["id"]
    assert task.status == GenerationTask.STATUS_PENDING


def test_duplicate_storyboard_submission_reuses_active_task(client, monkeypatch):
    workspace = write_workspace(episode_1_script="Episode 1 complete script")
    started = []
    monkeypatch.setattr(
        "studio.views._start_background_storyboard_generation",
        lambda task_id: started.append(task_id),
    )

    first_response = client.post(reverse("studio:generate_storyboard", args=[workspace["id"]]))
    second_response = client.post(reverse("studio:generate_storyboard", args=[workspace["id"]]))

    assert first_response.status_code == 302
    assert second_response.status_code == 302
    assert GenerationTask.objects.count() == 1
    assert started == [GenerationTask.objects.get().id]


def test_background_storyboard_generation_uses_task_workspace(monkeypatch):
    workspace = write_workspace(episode_1_script="Episode 1 complete script")
    repository = WorkspaceRepository()
    task = repository.create_storyboard_task(workspace["id"], 1)
    provider = object()
    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: provider)
    monkeypatch.setattr(
        "studio.views.generate_storyboard",
        lambda llm, episode_script, episode_number=1: storyboard_payload(),
    )

    from studio.views import _run_storyboard_generation

    _run_storyboard_generation(task["id"])

    saved = read_workspace(workspace["id"])
    finished_task = repository.get_task(task["id"])
    assert len(saved["storyboard_prompts"]) == 12
    assert finished_task["status"] == GenerationTask.STATUS_SUCCEEDED
    assert finished_task["workspace_id"] == workspace["id"]
    assert finished_task["result_snapshot"] == {"episode_number": 1, "storyboard_prompt_count": 12}


def test_background_storyboard_error_keeps_previous_content(monkeypatch):
    workspace = write_workspace(
        episode_1_script="keep old script",
        storyboard_prompts=storyboard_payload(),
    )
    repository = WorkspaceRepository()
    task = repository.create_storyboard_task(workspace["id"], 1)
    provider = object()
    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: provider)

    def raise_error(llm, episode_script, episode_number=1):
        raise ValueError("storyboard generation failed")

    monkeypatch.setattr("studio.views.generate_storyboard", raise_error)

    from studio.views import _run_storyboard_generation

    _run_storyboard_generation(task["id"])

    saved = read_workspace(workspace["id"])
    failed_task = repository.get_task(task["id"])
    assert saved["episode_1_script"] == "keep old script"
    assert saved["storyboard_prompts"] == storyboard_payload()
    assert failed_task["status"] == GenerationTask.STATUS_FAILED
    assert "storyboard generation failed" in failed_task["error_message"]


def test_task_status_returns_result_url_for_task_workspace(client):
    workspace = write_workspace(episode_1_script="Episode 1 complete script")
    repository = WorkspaceRepository()
    task = repository.create_storyboard_task(workspace["id"], 1)
    repository.finish_task(task["id"], result={"storyboard_prompt_count": 12})

    response = client.get(reverse("studio:task_status", args=[task["id"]]))

    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace_id"] == workspace["id"]
    assert payload["status"] == GenerationTask.STATUS_SUCCEEDED
    assert payload["result_url"].endswith(reverse("studio:storyboard_episode", args=[workspace["id"], 1]))


def test_storyboard_page_polls_active_task(client):
    workspace = write_workspace(episode_1_script="Episode 1 complete script")
    task = WorkspaceRepository().create_storyboard_task(workspace["id"], 1)

    response = client.get(reverse("studio:storyboard", args=[workspace["id"]]))

    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "data-task-poller" in content
    assert reverse("studio:task_status", args=[task["id"]]) in content


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
    assert ">生成剧本</button>" not in content


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

def test_series_plan_creates_sixty_episode_records():
    workspace = write_workspace(
        script_plan=script_payload()["script_plan"],
        episode_1_script=script_payload()["episode_1_script"],
    )

    assert len(workspace["episodes"]) == 60
    assert workspace["episodes"][0]["episode"] == 1
    assert workspace["episodes"][0]["has_script"] is True
    assert workspace["episodes"][11]["episode"] == 12
    assert workspace["episodes"][11]["has_script"] is False


def test_generate_arbitrary_episode_script_starts_background_task(client, monkeypatch):
    workspace = write_workspace(
        script_plan=script_payload()["script_plan"],
        episode_1_script=script_payload()["episode_1_script"],
    )
    started = []
    monkeypatch.setattr(
        "studio.views._start_background_episode_script_generation",
        lambda task_id: started.append(task_id),
    )

    response = client.post(
        reverse("studio:generate_episode_script", args=[workspace["id"], 12])
    )

    task = GenerationTask.objects.get(task_type=GenerationTask.TYPE_EPISODE_SCRIPT)
    episode = WorkspaceRepository().get_episode(workspace["id"], 12)
    assert response.status_code == 302
    assert response["Location"] == reverse(
        "studio:episode_script",
        args=[workspace["id"], 12],
    )
    assert task.target_id == "12"
    assert started == [task.id]
    assert episode["script_status"] == "generating"


def test_background_episode_script_generation_saves_requested_episode(monkeypatch):
    workspace = write_workspace(
        script_plan=script_payload()["script_plan"],
        episode_1_script=script_payload()["episode_1_script"],
    )
    repository = WorkspaceRepository()
    task = repository.create_episode_script_task(workspace["id"], 12)
    captured = {}

    def fake_generate(provider, outline, episode, previous_episode=None, next_episode=None):
        captured["episode"] = episode["episode"]
        captured["previous"] = previous_episode["episode"]
        captured["next"] = next_episode["episode"]
        return "Episode 12 complete script"

    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: object())
    monkeypatch.setattr("studio.views.generate_episode_script", fake_generate)

    from studio.views import _run_episode_script_generation

    _run_episode_script_generation(task["id"])

    episode = repository.get_episode(workspace["id"], 12)
    finished_task = repository.get_task(task["id"])
    assert captured == {"episode": 12, "previous": 11, "next": 13}
    assert episode["full_script"] == "Episode 12 complete script"
    assert episode["script_status"] == "ready"
    assert finished_task["status"] == GenerationTask.STATUS_SUCCEEDED


def test_storyboard_for_arbitrary_episode_is_saved_independently(monkeypatch):
    workspace = write_workspace(
        script_plan=script_payload()["script_plan"],
        episode_1_script=script_payload()["episode_1_script"],
    )
    repository = WorkspaceRepository()
    repository.save_episode_script(workspace["id"], 12, "Episode 12 complete script")
    task = repository.create_storyboard_task(workspace["id"], 12)
    captured = {}

    def fake_storyboard(provider, episode_script, episode_number=1):
        captured["script"] = episode_script
        captured["episode_number"] = episode_number
        return storyboard_payload()

    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: object())
    monkeypatch.setattr("studio.views.generate_storyboard", fake_storyboard)

    from studio.views import _run_storyboard_generation

    _run_storyboard_generation(task["id"])

    episode_12 = repository.get_episode_workspace(workspace["id"], 12)
    episode_1 = repository.get_episode_workspace(workspace["id"], 1)
    assert captured == {
        "script": "Episode 12 complete script",
        "episode_number": 12,
    }
    assert len(episode_12["storyboard_prompts"]) == 12
    assert episode_12["selected_episode"]["has_storyboard"] is True
    assert episode_1["storyboard_prompts"] == []


def test_task_status_points_to_requested_episode(client):
    workspace = write_workspace(
        script_plan=script_payload()["script_plan"],
        episode_1_script=script_payload()["episode_1_script"],
    )
    repository = WorkspaceRepository()
    repository.save_episode_script(workspace["id"], 12, "Episode 12 complete script")
    task = repository.create_storyboard_task(workspace["id"], 12)
    repository.finish_task(task["id"], result={"episode_number": 12})

    response = client.get(reverse("studio:task_status", args=[task["id"]]))

    assert response.status_code == 200
    assert response.json()["result_url"].endswith(
        reverse("studio:storyboard_episode", args=[workspace["id"], 12])
    )


def test_script_page_links_every_episode_to_its_own_workflow(client):
    workspace = write_workspace(
        script_plan=script_payload()["script_plan"],
        episode_1_script=script_payload()["episode_1_script"],
    )

    response = client.get(reverse("studio:script", args=[workspace["id"]]))

    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert reverse("studio:episode_script", args=[workspace["id"], 12]) in content
    assert reverse("studio:generate_episode_script", args=[workspace["id"], 12]) in content


def test_script_page_keeps_episode_modal_closed_until_requested(client):
    workspace = write_workspace(
        script_plan=script_payload()["script_plan"],
        episode_1_script=script_payload()["episode_1_script"],
    )

    response = client.get(reverse("studio:script", args=[workspace["id"]]))

    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert 'data-modal-target="episode-script-1"' in content
    assert "data-auto-open" not in content


def test_episode_script_route_auto_opens_requested_episode_modal(client):
    workspace = write_workspace(
        script_plan=script_payload()["script_plan"],
        episode_1_script=script_payload()["episode_1_script"],
    )

    response = client.get(
        reverse("studio:episode_script", args=[workspace["id"], 1])
    )

    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert 'id="episode-script-1" data-auto-open' in content


def test_generate_characters_starts_background_task(client, monkeypatch):
    workspace = write_workspace(
        script_plan=script_payload()["script_plan"],
        episode_1_script=script_payload()["episode_1_script"],
    )
    started = []
    monkeypatch.setattr(
        "studio.views._start_background_character_profile_generation",
        lambda task_id: started.append(task_id),
    )

    response = client.post(reverse("studio:generate_characters", args=[workspace["id"]]))

    task = GenerationTask.objects.get(task_type=GenerationTask.TYPE_CHARACTER_PROFILE)
    assert response.status_code == 302
    assert response["Location"] == reverse("studio:script", args=[workspace["id"]])
    assert started == [task.id]
    assert task.status == GenerationTask.STATUS_PENDING


def test_character_image_task_persists_edited_prompt_and_character_target():
    workspace = write_workspace(
        script_plan=script_payload()["script_plan"],
        episode_1_script=script_payload()["episode_1_script"],
    )
    repository = WorkspaceRepository()
    repository.save_character_profiles(workspace["id"], character_profiles())
    character = repository.get_workspace(workspace["id"])["characters"][0]

    task = repository.create_character_image_task(
        workspace["id"], character["id"], "Edited cinematic character prompt"
    )

    saved_character = repository.get_character(workspace["id"], character["id"])
    assert saved_character["image_prompt"] == "Edited cinematic character prompt"
    assert task["target_id"] == str(character["id"])
    assert task["input_snapshot"] == {
        "prompt": "Edited cinematic character prompt",
        "character_name": "Chen Mo",
    }


def test_script_page_shows_character_profile_failure(client):
    workspace = write_workspace(
        script_plan=script_payload()["script_plan"],
        episode_1_script=script_payload()["episode_1_script"],
    )
    repository = WorkspaceRepository()
    task = repository.create_character_profile_task(workspace["id"])
    repository.fail_task(
        task["id"],
        "模型网关暂时不可用（HTTP 502），请稍后重试。",
    )

    response = client.get(reverse("studio:script", args=[workspace["id"]]))

    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "角色设定生成失败" in content
    assert "模型网关暂时不可用（HTTP 502），请稍后重试。" in content
