import json

import pytest

from studio.repositories.workspace import JsonWorkspaceRepository


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


def test_get_workspace_raises_for_missing_file(tmp_path):
    repo = JsonWorkspaceRepository(tmp_path)

    with pytest.raises(FileNotFoundError):
        repo.get_workspace("missing")


def test_create_workspace_rejects_unknown_genre(tmp_path):
    repo = JsonWorkspaceRepository(tmp_path)

    with pytest.raises(ValueError, match="Unknown genre"):
        repo.create_workspace("未知题材", workspace_id="bad")
