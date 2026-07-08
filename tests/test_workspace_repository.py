import json

import pytest

from studio.constants import GENRES
from studio.repositories.workspace import JsonWorkspaceRepository, WorkspaceCorruptError


def test_create_workspace_writes_default_structure(tmp_path):
    repo = JsonWorkspaceRepository(tmp_path)

    data = repo.create_workspace("逆袭爽文", workspace_id="fixed-id")

    saved_path = tmp_path / "fixed-id.json"
    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    assert data == saved
    assert data["id"] == "fixed-id"
    assert data["genre"] == "逆袭爽文"
    assert data["episode_count"] == 60
    assert data["episode_duration_minutes"] == 2
    assert data["outlines"] == []
    assert data["selected_outline_id"] is None
    assert data["script_plan"] == []
    assert data["episode_1_script"] == ""
    assert data["storyboard_prompts"] == []
    assert data["created_at"]
    assert data["updated_at"]


def test_create_workspace_generates_unique_ids_in_same_second(tmp_path, monkeypatch):
    repo = JsonWorkspaceRepository(tmp_path)
    monkeypatch.setattr(repo, "_now", lambda: "2026-07-02T12:00:00+08:00")

    first = repo.create_workspace("逆袭爽文")
    second = repo.create_workspace("逆袭爽文")

    assert first["id"] != second["id"]
    assert (tmp_path / f"{first['id']}.json").exists()
    assert (tmp_path / f"{second['id']}.json").exists()


def test_replace_current_outline_set_overwrites_previous_outlines(tmp_path):
    repo = JsonWorkspaceRepository(tmp_path)
    first_outlines = [{"id": f"old-{index}", "title": "old"} for index in range(6)]
    second_outlines = [{"id": f"new-{index}", "title": "new"} for index in range(6)]

    repo.replace_current_outline_set(GENRES[0], first_outlines)
    repo.update_workspace(
        "current",
        selected_outline_id="old-1",
        script_plan=[{"episode": 1}],
        episode_1_script="old script",
        storyboard_prompts=[{"shot_number": 1}],
    )
    saved = repo.replace_current_outline_set(GENRES[1], second_outlines)

    assert saved["id"] == "current"
    assert saved["genre"] == GENRES[1]
    assert saved["outlines"] == second_outlines
    assert saved["selected_outline_id"] is None
    assert saved["script_plan"] == []
    assert saved["episode_1_script"] == ""
    assert saved["storyboard_prompts"] == []
    assert sorted(path.name for path in tmp_path.glob("*.json")) == ["current.json"]


def test_update_workspace_preserves_unrelated_fields(tmp_path):
    repo = JsonWorkspaceRepository(tmp_path)
    repo.create_workspace("都市修真", workspace_id="story-1")

    first_update = repo.update_workspace(
        "story-1",
        outlines=[{"id": "outline-1", "title": "title"}],
        selected_outline_id="outline-1",
        episode_1_script="first script",
    )
    second_update = repo.update_workspace(
        "story-1",
        outlines=[{"id": "outline-2", "title": "new title"}],
        selected_outline_id=None,
    )

    assert first_update["episode_1_script"] == "first script"
    assert second_update["episode_1_script"] == "first script"
    assert second_update["outlines"] == [{"id": "outline-2", "title": "new title"}]
    assert second_update["selected_outline_id"] is None


def test_save_workspace_writes_temp_file_then_replaces_target(tmp_path, monkeypatch):
    repo = JsonWorkspaceRepository(tmp_path)
    replace_calls = []
    original_replace = type(tmp_path).replace

    def spy_replace(self, target):
        replace_calls.append((self, target))
        return original_replace(self, target)

    monkeypatch.setattr(type(tmp_path), "replace", spy_replace)

    workspace = {
        "id": "story-atomic",
        "genre": "逆袭爽文",
        "episode_1_script": "complete json",
    }
    repo.save_workspace(workspace)

    target_path = tmp_path / "story-atomic.json"
    assert json.loads(target_path.read_text(encoding="utf-8"))["episode_1_script"] == "complete json"
    assert replace_calls
    temp_path, replaced_path = replace_calls[-1]
    assert temp_path.parent == tmp_path
    assert temp_path != target_path
    assert replaced_path == target_path
    assert not list(tmp_path.glob("*.tmp"))


def test_get_workspace_raises_for_missing_file(tmp_path):
    repo = JsonWorkspaceRepository(tmp_path)

    with pytest.raises(FileNotFoundError):
        repo.get_workspace("missing")


def test_get_workspace_raises_repository_error_for_corrupt_json(tmp_path):
    repo = JsonWorkspaceRepository(tmp_path)
    (tmp_path / "bad.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(WorkspaceCorruptError, match="bad"):
        repo.get_workspace("bad")


@pytest.mark.parametrize("workspace_id", ["a/b", "a\\b", "..", ""])
def test_path_for_rejects_unsafe_workspace_ids(tmp_path, workspace_id):
    repo = JsonWorkspaceRepository(tmp_path)

    with pytest.raises(ValueError, match="Invalid workspace id"):
        repo.path_for(workspace_id)


def test_create_workspace_rejects_unknown_genre(tmp_path):
    repo = JsonWorkspaceRepository(tmp_path)

    with pytest.raises(ValueError, match="Unknown genre"):
        repo.create_workspace("未知题材", workspace_id="bad")
