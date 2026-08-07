import hashlib
import json

import pytest

from studio.generation_handlers import _continuity_context
from studio.models import Episode, Outline, Project, Script, StoryboardPrompt
from studio.repositories.workspace import WorkspaceRepository


pytestmark = pytest.mark.django_db


def make_script():
    project = Project.objects.create(
        workspace_id="short-drama-v3",
        name="短剧 v3",
        genre="都市修真",
        episode_count=60,
        episode_duration_minutes=2,
    )
    outline = Outline.objects.create(
        project=project,
        outline_id="outline-v3",
        position=1,
        title="反转之夜",
        core_premise="主角追查真相",
        protagonist="林默",
        hook="身份反转",
        arc_summary="六十集主线",
    )
    project.selected_outline = outline
    project.save(update_fields=["selected_outline", "updated_at"])
    script = Script.objects.create(project=project, outline=outline, plan_payload=[])
    episodes = []
    for number in range(1, 4):
        episodes.append(
            Episode.objects.create(
                script=script,
                episode_number=number,
                title=f"第 {number} 集",
                summary=f"摘要 {number}",
                key_conflict=f"冲突 {number}",
                cliffhanger=f"悬念 {number}",
                full_script=f"剧本 {number}" if number < 3 else "",
                script_status=(
                    Episode.SCRIPT_READY if number < 3 else Episode.SCRIPT_PENDING
                ),
            )
        )
    return project, script, episodes


def test_continuity_uses_previous_carry_out_and_legacy_summary_fallback():
    previous = {
        "summary": "旧摘要",
        "cliffhanger": "旧悬念",
        "continuity_payload": {
            "carry_in": {},
            "state_delta": {},
            "carry_out": {"props": {"ring": "林默持有"}},
        },
    }

    assert _continuity_context(previous) == {"props": {"ring": "林默持有"}}
    assert _continuity_context(
        {"summary": "旧摘要", "cliffhanger": "旧悬念", "continuity_payload": {}}
    ) == {
        "legacy_summary": "旧摘要",
        "legacy_cliffhanger": "旧悬念",
    }


def test_changing_carry_out_marks_only_generated_downstream_episodes_stale():
    project, _, episodes = make_script()
    first, second, third = episodes
    first.continuity_payload = {
        "carry_in": {},
        "state_delta": {},
        "carry_out": {"knowledge": {"林默": ["旧线索"]}},
    }
    first.save(update_fields=["continuity_payload", "updated_at"])
    StoryboardPrompt.objects.create(
        project=project,
        script=first.script,
        episode=first,
        prompts_payload=[{"shot_number": 1}],
    )

    continuity = {
        "carry_in": {},
        "state_delta": {"knowledge": {"林默": ["发现真凶"]}},
        "carry_out": {"knowledge": {"林默": ["发现真凶"]}},
    }
    WorkspaceRepository().save_episode_script(
        project.workspace_id,
        1,
        "重写后的第一集",
        continuity_payload=continuity,
        pacing_payload={"duration_seconds": 75},
    )

    first.refresh_from_db()
    second.refresh_from_db()
    third.refresh_from_db()
    expected_hash = hashlib.sha256(
        json.dumps({}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert first.continuity_payload == continuity
    assert first.continuity_input_hash == expected_hash
    assert first.is_story_stale is False
    assert second.is_story_stale is True
    assert third.is_story_stale is False
    assert not StoryboardPrompt.objects.filter(episode=first).exists()


def test_storyboard_bundle_persists_cold_open_and_marks_source_shot_for_ui():
    project, _, episodes = make_script()
    prompts = [
        {"shot_number": 1, "beat_id": "beat_02_protagonist_goal"},
        {"shot_number": 2, "beat_id": "beat_05_resolution_or_reversal"},
    ]
    cold_open = {
        "hook_type": "reversal_dialogue",
        "source_beat_id": "beat_05_resolution_or_reversal",
        "source_shot_number": 2,
        "duration_seconds": 3,
        "withheld_reveal": "真相",
        "return_bridge": "两小时前",
        "trim_start_ms": 0,
        "trim_end_ms": 3000,
    }

    result = WorkspaceRepository().save_storyboard_for_episode(
        project.workspace_id,
        1,
        {"storyboard_prompts": prompts, "cold_open": cold_open},
    )

    assert result["cold_open_payload"] == cold_open
    assert result["storyboard_prompts"][0].get("is_cold_open_source", False) is False
    assert result["storyboard_prompts"][1]["is_cold_open_source"] is True
    assert result["storyboard_prompts"][1]["cold_open_trim_end_ms"] == 3000
