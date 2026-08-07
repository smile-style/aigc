import pytest

from studio.constants import GENRES
from studio.models import Outline, Project, Script, StoryboardPrompt
from studio.repositories.workspace import CURRENT_WORKSPACE_ID, WorkspaceRepository

pytestmark = pytest.mark.django_db


def outline_payload(prefix="outline"):
    return [
        {
            "id": f"{prefix}-{index}",
            "title": f"Outline title {index}",
            "core_premise": f"Core premise {index}",
            "protagonist": f"Protagonist {index}",
            "hook": f"Hook {index}",
            "arc_summary": f"Arc summary {index}",
        }
        for index in range(1, 7)
    ]


def test_create_workspace_writes_default_structure():
    repo = WorkspaceRepository()

    data = repo.create_workspace(GENRES[0], workspace_id="fixed-id")

    project = Project.objects.get(workspace_id="fixed-id")
    assert data["id"] == "fixed-id"
    assert project.workspace_id == "fixed-id"
    assert data["genre"] == GENRES[0]
    assert data["episode_count"] == 60
    assert data["episode_duration_minutes"] == 2
    assert data["episode_duration_label"] == "60–90 秒（目标 75 秒）"
    assert data["outlines"] == []
    assert data["selected_outline_id"] is None
    assert data["script_plan"] == []
    assert data["episode_1_script"] == ""
    assert data["storyboard_prompts"] == []
    assert data["created_at"]
    assert data["updated_at"]


def test_create_workspace_generates_unique_ids():
    repo = WorkspaceRepository()

    first = repo.create_workspace(GENRES[0])
    second = repo.create_workspace(GENRES[0])

    assert first["id"] != second["id"]
    assert Project.objects.filter(workspace_id__in=[first["id"], second["id"]]).count() == 2


def test_replace_current_outline_set_overwrites_previous_outlines_and_work_products():
    repo = WorkspaceRepository()

    repo.replace_current_outline_set(GENRES[0], outline_payload("old"))
    repo.update_workspace(CURRENT_WORKSPACE_ID, selected_outline_id="old-1")
    repo.update_workspace(
        CURRENT_WORKSPACE_ID,
        script_plan=[{"episode": 1}],
        episode_1_script="old script",
    )
    repo.update_workspace(
        CURRENT_WORKSPACE_ID,
        storyboard_prompts=[{"shot_number": 1}],
    )

    saved = repo.replace_current_outline_set(GENRES[1], outline_payload("new"))

    assert saved["id"] == CURRENT_WORKSPACE_ID
    assert saved["genre"] == GENRES[1]
    assert [outline["id"] for outline in saved["outlines"]] == [
        f"new-{index}" for index in range(1, 7)
    ]
    assert saved["selected_outline_id"] is None
    assert saved["script_plan"] == []
    assert saved["episode_1_script"] == ""
    assert saved["storyboard_prompts"] == []
    assert Project.objects.count() == 1
    assert Outline.objects.count() == 6
    assert Script.objects.count() == 0
    assert StoryboardPrompt.objects.count() == 0


def test_update_workspace_preserves_unrelated_fields():
    repo = WorkspaceRepository()
    repo.create_workspace(GENRES[0], workspace_id="story-1")
    repo.update_workspace("story-1", outlines=outline_payload("outline"))
    repo.update_workspace("story-1", selected_outline_id="outline-1")
    first_update = repo.update_workspace(
        "story-1",
        script_plan=[{"episode": 1, "title": "old title"}],
        episode_1_script="first script",
    )
    second_update = repo.update_workspace("story-1", selected_outline_id="outline-2")

    assert first_update["episode_1_script"] == "first script"
    assert second_update["episode_1_script"] == ""
    assert second_update["script_plan"] == []
    assert second_update["selected_outline_id"] == "outline-2"


def test_get_workspace_raises_for_missing_project():
    repo = WorkspaceRepository()

    with pytest.raises(FileNotFoundError):
        repo.get_workspace("missing")


def test_create_workspace_rejects_unknown_genre():
    repo = WorkspaceRepository()

    with pytest.raises(ValueError, match="Unknown genre"):
        repo.create_workspace("unknown", workspace_id="bad")

def test_script_pacing_and_plan_are_persisted_per_episode():
    repo = WorkspaceRepository()
    repo.create_workspace(GENRES[0], workspace_id="pacing-workspace")
    repo.update_workspace("pacing-workspace", outlines=outline_payload("pacing"))
    repo.update_workspace("pacing-workspace", selected_outline_id="pacing-1")
    plan = [
        {
            "episode": 1,
            "title": "危机开场",
            "summary": "主角必须在限时内取回货款",
            "key_conflict": "货款和仓库同时出事",
            "cliffhanger": "灾难提前发生",
            "episode_goal": "取回货款",
            "obstacle_1": "钱不到账",
            "obstacle_2": "仓库被抢",
            "resolution_or_reversal": "追回货物却暴露位置",
            "next_crisis": "灾难提前",
        }
    ]
    pacing = {
        "duration_seconds": 85,
        "beats": [{"beat_type": "crisis_open"}],
        "information_beats": [
            {"at_second": 0, "information": "仓库遇袭", "consequence": "必须立即行动"}
        ],
        "profile_version": "short_drama_v1_85s",
    }

    saved = repo.save_script_for_outline(
        "pacing-workspace",
        "pacing-1",
        plan,
        "第 1 集完整剧本",
        episode_1_pacing=pacing,
    )

    episode = saved["episodes"][0]
    assert episode["episode_goal"] == "取回货款"
    assert episode["obstacle_1"] == "钱不到账"
    assert episode["obstacle_2"] == "仓库被抢"
    assert episode["next_crisis"] == "灾难提前"
    assert episode["pacing_payload"] == pacing
    assert saved["episode_1_pacing"] == pacing
