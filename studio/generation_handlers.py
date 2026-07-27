from studio.models import GenerationTask, ModelAssignment, Script
from studio.repositories.workspace import WorkspaceRepository
from studio.services.characters import (
    apply_character_visual_style,
    generate_character_profiles,
)
from studio.services.covers import (
    COVER_GENERATION_SIZE,
    save_cover_template,
)
from studio.services.model_config import image_provider_for, llm_provider_for
from studio.services.script import generate_episode_script, generate_script
from studio.services.storyboard import generate_storyboard


def handle_script_generation(task):
    repository = WorkspaceRepository()
    workspace_id = task.project.workspace_id
    outline_id = task.target_id
    workspace = repository.get_workspace(workspace_id)
    outline = next(
        (item for item in workspace.get("outlines", []) if item.get("id") == outline_id),
        None,
    )
    if outline is None:
        outline = next(
            (item for item in workspace.get("usable_outlines", []) if item.get("id") == outline_id),
            None,
        )
    if outline is None:
        raise FileNotFoundError(f"Outline not found: {outline_id}")
    payload = generate_script(
        llm_provider_for(ModelAssignment.PURPOSE_SCRIPT),
        outline,
    )
    repository.save_script_for_outline(
        workspace_id,
        outline_id,
        payload["script_plan"],
        payload["episode_1_script"],
        episode_1_pacing=payload["episode_1_pacing"],
    )
    return {
        "outline_id": outline_id,
        "episode_count": len(payload["script_plan"]),
    }


def handle_episode_script_generation(task):
    repository = WorkspaceRepository()
    workspace_id = task.project.workspace_id
    snapshot = task.input_snapshot or {}
    script_id = snapshot.get("script_id")
    episode_number = int(snapshot.get("episode") or task.target_id)
    workspace = repository.get_workspace(workspace_id, script_id=script_id)
    episodes = workspace.get("episodes", [])
    episode = next(item for item in episodes if item.get("episode") == episode_number)
    previous_episode = next(
        (item for item in episodes if item.get("episode") == episode_number - 1),
        None,
    )
    next_episode = next(
        (item for item in episodes if item.get("episode") == episode_number + 1),
        None,
    )
    generated = generate_episode_script(
        llm_provider_for(ModelAssignment.PURPOSE_EPISODE_SCRIPT),
        workspace["selected_outline"],
        episode,
        previous_episode=previous_episode,
        next_episode=next_episode,
    )
    if isinstance(generated, dict):
        full_script = generated["episode_script"]
        pacing_payload = generated.get("pacing")
    else:
        full_script = generated
        pacing_payload = None
    repository.save_episode_script(
        workspace_id,
        episode_number,
        full_script,
        script_id=script_id,
        pacing_payload=pacing_payload,
    )
    return {"episode_number": episode_number}


def handle_storyboard_generation(task):
    repository = WorkspaceRepository()
    workspace_id = task.project.workspace_id
    snapshot = task.input_snapshot or {}
    episode_number = int(snapshot.get("episode_number") or task.target_id or 1)
    script_id = snapshot.get("script_id")
    episode = repository.get_episode(
        workspace_id,
        episode_number,
        script_id=script_id,
    )
    kwargs = {}
    if episode.get("pacing_payload"):
        kwargs["pacing"] = episode["pacing_payload"]
    prompts = generate_storyboard(
        llm_provider_for(ModelAssignment.PURPOSE_STORYBOARD),
        episode["full_script"],
        episode_number=episode_number,
        **kwargs,
    )
    repository.save_storyboard_for_episode(
        workspace_id,
        episode_number,
        prompts,
        script_id=script_id,
    )
    return {
        "episode_number": episode_number,
        "storyboard_prompt_count": len(prompts),
    }


def handle_character_profile_generation(task):
    repository = WorkspaceRepository()
    workspace_id = task.project.workspace_id
    snapshot = task.input_snapshot or {}
    script_id = snapshot.get("script_id")
    workspace = repository.get_workspace(workspace_id, script_id=script_id)
    episode_number = snapshot.get("episode_number")
    episode_focus = None
    if episode_number is not None:
        episode_number = int(episode_number)
        episode_focus = next(
            (
                item
                for item in workspace.get("episodes", [])
                if int(item.get("episode") or 0) == episode_number
            ),
            None,
        )
        if episode_focus is None:
            raise FileNotFoundError(f"Episode not found: {episode_number}")
    existing_names = [
        item["name"] for item in workspace.get("characters", []) if item.get("name")
    ]
    profiles = generate_character_profiles(
        llm_provider_for(ModelAssignment.PURPOSE_CHARACTER_PROFILE),
        workspace["selected_outline"],
        workspace.get("episodes", []),
        snapshot.get("visual_style", workspace["character_visual_style"]),
        episode_focus=episode_focus,
        existing_character_names=existing_names,
    )
    repository.save_character_profiles(
        workspace_id,
        profiles,
        script_id=script_id,
        preserve_existing=episode_focus is not None,
    )
    profile_names = [profile["name"] for profile in profiles]
    reused_names = []
    if episode_focus:
        full_script = str(episode_focus.get("full_script") or "")
        reused_names = [name for name in existing_names if name in full_script]
    character_names = list(dict.fromkeys([*reused_names, *profile_names]))
    return {
        "character_count": len(character_names),
        "character_names": character_names,
        "new_character_names": profile_names,
        "reused_character_names": reused_names,
    }


def handle_character_image_generation(task):
    repository = WorkspaceRepository()
    workspace_id = task.project.workspace_id
    character_id = int(task.target_id)
    character = repository.get_character(workspace_id, character_id)
    prompt = apply_character_visual_style(
        character["image_prompt"],
        (task.input_snapshot or {}).get(
            "visual_style",
            character["visual_style"],
        ),
    )
    result = image_provider_for().generate_image(prompt)
    asset = repository.save_character_asset(
        workspace_id,
        character_id,
        result,
        prompt_snapshot=prompt,
    )
    return {
        "character_id": character_id,
        "image_url": asset["image_url"],
    }


def handle_cover_generation(task):
    repository = WorkspaceRepository()
    snapshot = task.input_snapshot or {}
    script = Script.objects.select_related("outline", "project").get(
        pk=snapshot["script_id"],
        project=task.project,
    )
    prompt = snapshot["prompt"]
    result = image_provider_for().generate_image(
        prompt,
        size=COVER_GENERATION_SIZE,
    )
    template = save_cover_template(script, result, prompt)
    return {
        "script_id": script.id,
        "cover_template_id": template.id,
        "cover_version": template.version,
    }


HANDLERS = {
    GenerationTask.TYPE_SCRIPT: handle_script_generation,
    GenerationTask.TYPE_EPISODE_SCRIPT: handle_episode_script_generation,
    GenerationTask.TYPE_STORYBOARD: handle_storyboard_generation,
    GenerationTask.TYPE_CHARACTER_PROFILE: handle_character_profile_generation,
    GenerationTask.TYPE_CHARACTER_IMAGE: handle_character_image_generation,
    GenerationTask.TYPE_COVER_IMAGE: handle_cover_generation,
}
