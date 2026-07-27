import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from studio.models import (
    Character,
    CharacterAsset,
    Episode,
    EpisodeWorkflowRun,
    GenerationTask,
    Outline,
    Project,
    Script,
    StoryboardPrompt,
    SubtitleTrack,
    VideoAsset,
    VideoComposition,
)
from studio.services.episode_workflow import (
    advance_episode_workflow,
    create_episode_workflow,
    retry_episode_workflow,
    workflow_payload,
)
from studio.services.subtitles import subtitle_source_hash
from studio.services.video import queue_export, sync_storyboard_shots


pytestmark = pytest.mark.django_db


def make_episode(full_script=""):
    project = Project.objects.create(
        workspace_id="workflow-space",
        name="Workflow project",
        genre="Drama",
        episode_count=10,
        episode_duration_minutes=2,
    )
    outline = Outline.objects.create(
        project=project,
        outline_id="workflow-outline",
        position=1,
        title="Workflow outline",
        core_premise="Premise",
        protagonist="Lead",
        hook="Hook",
        arc_summary="Arc",
        is_usable=True,
    )
    project.selected_outline = outline
    project.save(update_fields=["selected_outline"])
    script = Script.objects.create(
        project=project,
        outline=outline,
        plan_payload=[],
    )
    episode = Episode.objects.create(
        script=script,
        episode_number=1,
        title="Episode one",
        summary="Summary",
        key_conflict="Conflict",
        cliffhanger="Cliffhanger",
        full_script=full_script,
        script_status=(
            Episode.SCRIPT_READY if full_script else Episode.SCRIPT_PENDING
        ),
    )
    return project, script, episode


def make_ready_shot(episode):
    storyboard = StoryboardPrompt.objects.create(
        project=episode.script.project,
        script=episode.script,
        episode=episode,
        prompts_payload=[
            {
                "shot_number": 1,
                "duration": "4s",
                "visual_description": "Street",
                "character_action": "Walk",
                "dialogue_or_narration": "Hello",
                "camera_language": "Wide shot",
                "image_prompt": "street",
                "video_prompt": "Walking in a street",
            }
        ],
    )
    shot = sync_storyboard_shots(storyboard)[0]
    asset = VideoAsset.objects.create(
        shot=shot,
        version=1,
        status=VideoAsset.STATUS_READY,
        prompt_snapshot="Walking in a street",
        video="videos/shot.mp4",
        is_selected=True,
    )
    return shot, asset


def test_workflow_starts_script_task_and_reuses_active_run():
    _, _, episode = make_episode()

    run, created = create_episode_workflow(episode)
    reused, reused_created = create_episode_workflow(episode)

    assert created is True
    assert reused_created is False
    assert reused.id == run.id
    assert run.stage == EpisodeWorkflowRun.STAGE_SCRIPT
    assert run.child_task.task_type == GenerationTask.TYPE_EPISODE_SCRIPT
    assert run.status == EpisodeWorkflowRun.STATUS_RUNNING


def test_one_click_character_stage_generates_only_characters_in_at_least_six_shots():
    _, script, episode = make_episode(full_script="Episode script")
    existing = Character.objects.create(
        script=script,
        name="Existing lead",
        role="Lead",
        appearance="Black hair",
        personality="Calm",
        costume="Black coat",
        image_prompt="Existing lead reference",
    )
    CharacterAsset.objects.create(
        character=existing,
        model="image-model",
        prompt_snapshot="Existing lead reference",
        image="characters/existing.png",
    )

    run, _ = create_episode_workflow(episode)

    assert run.stage == EpisodeWorkflowRun.STAGE_STORYBOARD
    assert run.child_task.task_type == GenerationTask.TYPE_STORYBOARD

    prompts = []
    for shot_number in range(1, 7):
        names = ["Existing lead", "New doctor"]
        if shot_number <= 5:
            names.append("Five-shot extra")
        prompts.append(
            {
                "shot_number": shot_number,
                "duration": "4s",
                "visual_description": f"Hospital shot {shot_number}",
                "character_action": "Talk",
                "dialogue_or_narration": "Dialogue",
                "camera_language": "Medium shot",
                "image_prompt": "hospital",
                "video_prompt": "People talk in a hospital",
                "character_names": names,
            }
        )
    StoryboardPrompt.objects.create(
        project=episode.script.project,
        script=script,
        episode=episode,
        prompts_payload=prompts,
    )
    GenerationTask.objects.filter(pk=run.child_task_id).update(
        status=GenerationTask.STATUS_SUCCEEDED,
    )

    run = advance_episode_workflow(run.id)

    new_character = Character.objects.get(script=script, name="New doctor")
    assert run.stage == EpisodeWorkflowRun.STAGE_CHARACTERS
    assert not Character.objects.filter(script=script, name="Five-shot extra").exists()
    assert run.details["character_shot_threshold"] == 6
    assert run.details["character_shot_counts"] == {
        "Existing lead": 6,
        "New doctor": 6,
    }
    assert run.details["character_new_total"] == 1
    assert run.details["character_reused"] == 1
    assert run.details["character_total"] == 1
    assert run.details["character_ready"] == 0
    assert len(run.details["character_image_task_ids"]) == 1
    image_task = GenerationTask.objects.get(pk=run.details["character_image_task_ids"][0])
    assert image_task.target_id == str(new_character.id)

    CharacterAsset.objects.create(
        character=new_character,
        model="image-model",
        prompt_snapshot=new_character.image_prompt,
        image="characters/new-doctor.png",
    )
    GenerationTask.objects.filter(pk=image_task.id).update(
        status=GenerationTask.STATUS_SUCCEEDED,
    )
    run = advance_episode_workflow(run.id)

    assert run.stage == EpisodeWorkflowRun.STAGE_VIDEOS
    assert run.details["character_new_total"] == 1
    assert run.details["character_reused"] == 1


def test_failed_child_is_automatically_retried_three_times_before_workflow_fails():
    _, _, episode = make_episode()
    run, _ = create_episode_workflow(episode)
    task_ids = []

    for retry_count in range(1, 4):
        task_ids.append(run.child_task_id)
        GenerationTask.objects.filter(pk=run.child_task_id).update(
            status=GenerationTask.STATUS_FAILED,
            error_message="invalid generated pacing",
        )

        run = advance_episode_workflow(run.id)

        assert run.status == EpisodeWorkflowRun.STATUS_RUNNING
        assert run.child_task_id is None
        assert run.details["auto_retry_count"] == retry_count
        assert run.details["auto_retry_message"] == "invalid generated pacing"

        run = advance_episode_workflow(run.id)
        assert run.child_task_id not in task_ids

    GenerationTask.objects.filter(pk=run.child_task_id).update(
        status=GenerationTask.STATUS_FAILED,
        error_message="invalid generated pacing",
    )

    run = advance_episode_workflow(run.id)

    assert run.status == EpisodeWorkflowRun.STATUS_FAILED
    assert run.error_message == "invalid generated pacing"
    assert run.details["auto_retry_count"] == 3


def test_successful_stage_transition_resets_automatic_retry_count():
    _, _, episode = make_episode()
    run, _ = create_episode_workflow(episode)
    GenerationTask.objects.filter(pk=run.child_task_id).update(
        status=GenerationTask.STATUS_FAILED,
        error_message="invalid generated pacing",
    )
    run = advance_episode_workflow(run.id)
    run = advance_episode_workflow(run.id)
    script_task_id = run.child_task_id
    episode.full_script = "Generated script"
    episode.script_status = Episode.SCRIPT_READY
    episode.save(update_fields=["full_script", "script_status"])
    GenerationTask.objects.filter(pk=script_task_id).update(
        status=GenerationTask.STATUS_SUCCEEDED,
    )

    run = advance_episode_workflow(run.id)

    assert run.stage == EpisodeWorkflowRun.STAGE_STORYBOARD
    assert run.details.get("auto_retry_count") is None
    assert run.details.get("auto_retry_message") is None


def test_manual_retry_resets_automatic_retry_budget():
    _, _, episode = make_episode()
    run, _ = create_episode_workflow(episode)
    run.status = EpisodeWorkflowRun.STATUS_FAILED
    run.error_message = "still invalid"
    run.details = {
        "auto_retry_count": 3,
        "auto_retry_message": "still invalid",
    }
    run.save()

    run = retry_episode_workflow(run)

    assert run.status == EpisodeWorkflowRun.STATUS_RUNNING
    assert run.details.get("auto_retry_count") is None
    assert run.details.get("auto_retry_message") is None
    assert run.child_task.task_type == GenerationTask.TYPE_EPISODE_SCRIPT


def test_workflow_payload_reports_ordered_steps():
    _, _, episode = make_episode()
    run, _ = create_episode_workflow(episode)

    payload = workflow_payload(run)

    assert payload["progress_percent"] == 8
    assert payload["steps"][0]["key"] == EpisodeWorkflowRun.STAGE_SCRIPT
    assert payload["steps"][0]["status"] == "running"
    assert payload["steps"][-1]["key"] == EpisodeWorkflowRun.STAGE_CAPTIONED_EXPORT
    assert payload["auto_retry_count"] == 0
    assert payload["auto_retry_max"] == 3
    assert payload["auto_retrying"] is False


def test_workflow_partial_shows_automatic_retry_progress():
    _, _, episode = make_episode()
    run, _ = create_episode_workflow(episode)
    run.details = {
        "auto_retry_count": 1,
        "auto_retry_message": "invalid pacing",
    }
    run.save(update_fields=["details"])

    html = render_to_string(
        "studio/_episode_workflow.html",
        {"workflow": workflow_payload(run)},
    )

    assert "1/3" in html
    assert "invalid pacing" in html


def test_start_workflow_view_redirects_to_episode_context(client):
    project, script, episode = make_episode()

    response = client.post(
        reverse(
            "studio:start_episode_workflow",
            args=[project.workspace_id, episode.episode_number],
        ),
        {"script_id": script.id},
    )

    assert response.status_code == 302
    assert reverse(
        "studio:script_episode_context",
        args=[project.workspace_id, script.id, episode.episode_number],
    ) in response.url
    assert EpisodeWorkflowRun.objects.filter(episode=episode).exists()


def test_queue_export_keeps_clean_and_captioned_variants_separate():
    _, _, episode = make_episode(full_script="Script")
    shot, _ = make_ready_shot(episode)

    clean_task, clean_created = queue_export(episode, include_subtitles=False)
    clean = VideoComposition.objects.get(pk=clean_task.target_id)

    track = SubtitleTrack.objects.create(
        episode=episode,
        enabled=True,
        status=SubtitleTrack.STATUS_CONFIRMED,
    )
    track.cues.create(
        shot=shot,
        position=1,
        source_text="Hello",
        text="Hello",
        start_ms=0,
        end_ms=1200,
    )
    track.source_hash = subtitle_source_hash(episode)
    track.save(update_fields=["source_hash"])
    captioned_task, captioned_created = queue_export(
        episode,
        include_subtitles=True,
    )
    captioned = VideoComposition.objects.get(pk=captioned_task.target_id)

    assert clean_created is True
    assert captioned_created is True
    assert clean.variant == VideoComposition.VARIANT_CLEAN
    assert clean.include_subtitles is False
    assert captioned.variant == VideoComposition.VARIANT_CAPTIONED
    assert captioned.include_subtitles is True
    assert captioned.subtitle_snapshot["cues"][0]["text"] == "Hello"


def test_video_page_disables_captioned_download_without_subtitles(client):
    project, script, episode = make_episode(full_script="Script")
    make_ready_shot(episode)
    VideoComposition.objects.create(
        episode=episode,
        version=1,
        variant=VideoComposition.VARIANT_CLEAN,
        status=VideoComposition.STATUS_READY,
        video="videos/clean.mp4",
    )

    response = client.get(
        reverse(
            "studio:video_script_episode",
            args=[project.workspace_id, script.id, episode.episode_number],
        ),
        {"tab": "assembly"},
    )
    content = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "variant=clean" in content
    assert "&#19979;&#36733;&#26377;&#23383;&#24149;&#29256;&#26412;" in content
    assert "disabled" in content
