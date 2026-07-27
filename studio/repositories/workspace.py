import uuid
from pathlib import Path

from django.core.files.base import ContentFile

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from studio.constants import (
    EPISODE_COUNT,
    EPISODE_DURATION_LABEL,
    EPISODE_DURATION_MINUTES,
    GENRES,
    PACING_PROFILE_VERSION,
)
from studio.models import Character, CharacterAsset, Episode, GenerationTask, Outline, Project, Script, StoryboardPrompt

CURRENT_WORKSPACE_ID = "current"


class WorkspaceCorruptError(Exception):
    pass


class WorkspaceRepository:
    def create_workspace(self, genre, workspace_id=None):
        if genre not in GENRES:
            raise ValueError(f"Unknown genre: {genre}")

        workspace_id = workspace_id or uuid.uuid4().hex
        project, created = Project.objects.get_or_create(
            workspace_id=workspace_id,
            defaults={
                "name": "Current Project" if workspace_id == CURRENT_WORKSPACE_ID else workspace_id,
                "genre": genre,
                "episode_count": EPISODE_COUNT,
                "episode_duration_minutes": EPISODE_DURATION_MINUTES,
            },
        )
        if not created:
            project.genre = genre
            project.episode_count = EPISODE_COUNT
            project.episode_duration_minutes = EPISODE_DURATION_MINUTES
            project.save(
                update_fields=[
                    "genre",
                    "episode_count",
                    "episode_duration_minutes",
                    "updated_at",
                ]
            )
        return self._workspace_to_dict(project)

    def get_workspace(self, workspace_id, script_id=None):
        try:
            project = (
                Project.objects.select_related(
                    "selected_outline",
                    "selected_outline__script",
                )
                .prefetch_related("outlines", "outlines__script")
                .get(workspace_id=workspace_id)
            )
        except Project.DoesNotExist as exc:
            raise FileNotFoundError(f"Workspace not found: {workspace_id}") from exc
        script = None
        if script_id is not None:
            try:
                script = Script.objects.select_related("outline", "project").get(
                    pk=script_id,
                    project=project,
                )
            except Script.DoesNotExist as exc:
                raise FileNotFoundError(f"Script not found: {script_id}") from exc
        return self._workspace_to_dict(project, script_override=script)

    def get_project_workspace(self, project_id):
        try:
            outline = (
                Outline.objects.select_related("project", "script")
                .get(pk=project_id, is_usable=True)
            )
        except Outline.DoesNotExist as exc:
            raise FileNotFoundError(f"Project not found: {project_id}") from exc
        return self._workspace_to_dict(
            outline.project,
            outline_override=outline,
        )

    def save_workspace(self, workspace):
        workspace_id = workspace["id"]
        project = Project.objects.get(workspace_id=workspace_id)
        return self.update_workspace(
            workspace_id,
            genre=workspace.get("genre", project.genre),
            outlines=workspace.get("outlines"),
            selected_outline_id=workspace.get("selected_outline_id"),
            script_plan=workspace.get("script_plan"),
            episode_1_script=workspace.get("episode_1_script"),
            storyboard_prompts=workspace.get("storyboard_prompts"),
        )

    def get_current_workspace(self):
        return self.get_workspace(CURRENT_WORKSPACE_ID)

    def replace_current_outline_set(self, genre, outlines):
        if genre not in GENRES:
            raise ValueError(f"Unknown genre: {genre}")

        with transaction.atomic():
            project, _ = Project.objects.select_for_update().get_or_create(
                workspace_id=CURRENT_WORKSPACE_ID,
                defaults={
                    "name": "Current Project",
                    "genre": genre,
                    "episode_count": EPISODE_COUNT,
                    "episode_duration_minutes": EPISODE_DURATION_MINUTES,
                },
            )
            project.genre = genre
            project.episode_count = EPISODE_COUNT
            project.episode_duration_minutes = EPISODE_DURATION_MINUTES
            project.selected_outline = None
            project.save()
            StoryboardPrompt.objects.filter(project=project).delete()
            project.outlines.filter(is_usable=True).update(is_archived=True)
            project.outlines.filter(is_usable=False).delete()
            self._create_outlines(project, outlines, batch_id=uuid.uuid4().hex[:12])

        return self.get_workspace(CURRENT_WORKSPACE_ID)

    def update_workspace(self, workspace_id, **fields):
        with transaction.atomic():
            try:
                project = Project.objects.select_for_update().get(workspace_id=workspace_id)
            except Project.DoesNotExist as exc:
                raise FileNotFoundError(f"Workspace not found: {workspace_id}") from exc

            if "genre" in fields and fields["genre"] is not None:
                if fields["genre"] not in GENRES:
                    raise ValueError(f"Unknown genre: {fields['genre']}")
                project.genre = fields["genre"]

            if "outlines" in fields and fields["outlines"] is not None:
                project.selected_outline = None
                StoryboardPrompt.objects.filter(project=project).delete()
                project.outlines.filter(is_usable=True).update(is_archived=True)
                project.outlines.filter(is_usable=False).delete()
                self._create_outlines(project, fields["outlines"], batch_id=uuid.uuid4().hex[:12])

            if "selected_outline_id" in fields:
                outline_id = fields["selected_outline_id"]
                if outline_id:
                    project.selected_outline = Outline.objects.get(
                        project=project,
                        outline_id=outline_id,
                    )
                else:
                    project.selected_outline = None

            project.save()

            script_plan = fields.get("script_plan")
            episode_1_script = fields.get("episode_1_script")
            if script_plan is not None or episode_1_script is not None:
                selected_outline = project.selected_outline
                if selected_outline is None:
                    raise ValueError("Selected outline is required before saving script")
                script, _ = Script.objects.get_or_create(
                    outline=selected_outline,
                    defaults={"project": project},
                )
                script.project = project
                if script_plan is not None:
                    script.plan_payload = script_plan
                if episode_1_script is not None:
                    script.episode_1_script = episode_1_script
                script.raw_payload = {
                    "script_plan": script.plan_payload,
                    "episode_1_script": script.episode_1_script,
                }
                script.save()
                self._sync_episodes(script, script.plan_payload, script.episode_1_script)
                selected_outline.script_status = Outline.SCRIPT_READY
                selected_outline.script_error = ""
                selected_outline.script_finished_at = timezone.now()
                selected_outline.save(
                    update_fields=[
                        "script_status",
                        "script_error",
                        "script_finished_at",
                    ]
                )

            if "storyboard_prompts" in fields and fields["storyboard_prompts"] is not None:
                script = self._script_for_selected_outline(project)
                if script is None:
                    raise ValueError("Script is required before saving storyboard prompts")
                episode = script.episodes.filter(episode_number=1).first()
                if episode is None:
                    raise ValueError("Episode 1 is required before saving storyboard prompts")
                StoryboardPrompt.objects.update_or_create(
                    episode=episode,
                    defaults={
                        "project": project,
                        "script": script,
                        "prompts_payload": fields["storyboard_prompts"],
                    },
                )

        return self.get_workspace(workspace_id)

    def start_script_generation(self, workspace_id, outline_id=None):
        with transaction.atomic():
            try:
                project = Project.objects.select_for_update().get(workspace_id=workspace_id)
            except Project.DoesNotExist as exc:
                raise FileNotFoundError(f"Workspace not found: {workspace_id}") from exc

            if outline_id:
                try:
                    outline = Outline.objects.select_for_update().get(
                        project=project,
                        outline_id=outline_id,
                    )
                except Outline.DoesNotExist as exc:
                    raise FileNotFoundError(f"Outline not found: {outline_id}") from exc
                project.selected_outline = outline
                project.save(update_fields=["selected_outline", "updated_at"])
            else:
                outline = project.selected_outline
                if outline is None:
                    raise ValueError("请先选择一个大纲，再生成剧本。")
                outline = Outline.objects.select_for_update().get(pk=outline.pk)

            active = project.generation_tasks.filter(
                task_type=GenerationTask.TYPE_SCRIPT,
                target_id=outline.outline_id,
                status__in=[
                    GenerationTask.STATUS_PENDING,
                    GenerationTask.STATUS_RUNNING,
                    GenerationTask.STATUS_RETRY_WAIT,
                ],
            ).order_by("-created_at", "-id").first()
            if active:
                data = self._outline_to_dict(outline)
                data["generation_task_id"] = active.id
                data["created"] = False
                return data

            outline.script_status = Outline.SCRIPT_GENERATING
            outline.script_error = ""
            outline.script_started_at = timezone.now()
            outline.script_finished_at = None
            outline.save(
                update_fields=[
                    "script_status",
                    "script_error",
                    "script_started_at",
                    "script_finished_at",
                ]
            )

            task = GenerationTask.objects.create(
                project=project,
                task_type=GenerationTask.TYPE_SCRIPT,
                target_id=outline.outline_id,
                input_snapshot={
                    "outline_id": outline.outline_id,
                    "project_id": outline.id,
                },
                idempotency_key=f"script:{project.id}:{outline.id}",
            )
            data = self._outline_to_dict(outline)
            data["generation_task_id"] = task.id
            data["created"] = True
            return data

    def save_script_for_outline(
        self,
        workspace_id,
        outline_id,
        script_plan,
        episode_1_script,
        episode_1_pacing=None,
    ):
        with transaction.atomic():
            try:
                project = Project.objects.select_for_update().get(workspace_id=workspace_id)
            except Project.DoesNotExist as exc:
                raise FileNotFoundError(f"Workspace not found: {workspace_id}") from exc

            try:
                outline = Outline.objects.select_for_update().get(
                    project=project,
                    outline_id=outline_id,
                )
            except Outline.DoesNotExist as exc:
                raise FileNotFoundError(f"Outline not found: {outline_id}") from exc

            script, _ = Script.objects.update_or_create(
                outline=outline,
                defaults={
                    "project": project,
                    "plan_payload": script_plan,
                    "episode_1_script": episode_1_script,
                    "raw_payload": {
                        "script_plan": script_plan,
                        "episode_1_script": episode_1_script,
                        "episode_1_pacing": episode_1_pacing or {},
                        "pacing_profile_version": PACING_PROFILE_VERSION,
                    },
                },
            )
            outline.script_status = Outline.SCRIPT_READY
            outline.script_error = ""
            outline.script_finished_at = timezone.now()
            outline.save(
                update_fields=[
                    "script_status",
                    "script_error",
                    "script_finished_at",
                ]
            )
            self._sync_episodes(
                script,
                script_plan,
                episode_1_script,
                episode_1_pacing=episode_1_pacing,
            )
            StoryboardPrompt.objects.filter(project=project).exclude(script=script).delete()

        return self.get_workspace(workspace_id)

    def mark_script_generation_failed(self, workspace_id, outline_id, error):
        with transaction.atomic():
            try:
                outline = Outline.objects.select_for_update().get(
                    project__workspace_id=workspace_id,
                    outline_id=outline_id,
                )
            except Outline.DoesNotExist as exc:
                raise FileNotFoundError(f"Outline not found: {outline_id}") from exc

            outline.script_status = Outline.SCRIPT_FAILED
            outline.script_error = str(error)
            outline.script_finished_at = timezone.now()
            outline.save(
                update_fields=[
                    "script_status",
                    "script_error",
                    "script_finished_at",
                ]
            )

    @staticmethod
    def _storyboard_task_target(script_id, episode_number):
        return f"script:{script_id}:episode:{episode_number}" if script_id else str(episode_number)

    @staticmethod
    def _episode_task_target(script_id, episode_number):
        return f"script:{script_id}:episode:{episode_number}" if script_id else str(episode_number)


    def get_episode(self, workspace_id, episode_number, script_id=None):
        episode = self._get_episode_model(workspace_id, episode_number, script_id=script_id)
        return self._episode_to_dict(episode)

    def get_episode_workspace(self, workspace_id, episode_number, script_id=None):
        workspace = self.get_workspace(workspace_id, script_id=script_id)
        episode = self._get_episode_model(workspace_id, episode_number, script_id=script_id)
        project = episode.script.project
        workspace["selected_episode"] = self._episode_to_dict(episode)
        storyboard = getattr(episode, "storyboard_prompt", None)
        workspace["storyboard_prompts"] = storyboard.prompts_payload if storyboard else []
        storyboard_target = self._storyboard_task_target(script_id, episode_number)
        workspace["storyboard_task"] = self._latest_task_dict(
            project,
            GenerationTask.TYPE_STORYBOARD,
            target_id=storyboard_target,
        )
        workspace["episode_script_task"] = self._latest_task_dict(
            project,
            GenerationTask.TYPE_EPISODE_SCRIPT,
            target_id=self._episode_task_target(script_id, episode_number),
        )
        return workspace

    def create_episode_script_task(self, workspace_id, episode_number, script_id=None):
        with transaction.atomic():
            try:
                project = Project.objects.select_for_update().get(workspace_id=workspace_id)
            except Project.DoesNotExist as exc:
                raise FileNotFoundError(f"Workspace not found: {workspace_id}") from exc
            episode = self._get_episode_model(
                workspace_id,
                episode_number,
                for_update=True,
                script_id=script_id,
            )
            target_id = self._episode_task_target(script_id, episode_number)
            running_task = project.generation_tasks.filter(
                task_type=GenerationTask.TYPE_EPISODE_SCRIPT,
                target_id=target_id,
                status__in=[
                    GenerationTask.STATUS_PENDING,
                    GenerationTask.STATUS_RUNNING,
                    GenerationTask.STATUS_RETRY_WAIT,
                ],
            ).order_by("-created_at", "-id").first()
            if running_task:
                task_data = self._task_to_dict(running_task)
                task_data["created"] = False
                return task_data

            episode.script_status = Episode.SCRIPT_GENERATING
            episode.script_error = ""
            episode.script_started_at = timezone.now()
            episode.script_finished_at = None
            episode.save(
                update_fields=[
                    "script_status",
                    "script_error",
                    "script_started_at",
                    "script_finished_at",
                ]
            )
            input_snapshot = self._episode_to_dict(episode)
            input_snapshot["script_id"] = script_id
            task = GenerationTask.objects.create(
                project=project,
                task_type=GenerationTask.TYPE_EPISODE_SCRIPT,
                target_id=target_id,
                input_snapshot=input_snapshot,
            )
            task_data = self._task_to_dict(task)
            task_data["created"] = True
            return task_data

    def save_episode_script(
        self,
        workspace_id,
        episode_number,
        full_script,
        script_id=None,
        pacing_payload=None,
    ):
        if not isinstance(full_script, str) or not full_script.strip():
            raise ValueError("Episode script must be a non-empty string")
        with transaction.atomic():
            episode = self._get_episode_model(
                workspace_id,
                episode_number,
                for_update=True,
                script_id=script_id,
            )
            episode.full_script = full_script
            episode.script_status = Episode.SCRIPT_READY
            if isinstance(pacing_payload, dict):
                episode.pacing_payload = pacing_payload
            episode.script_error = ""
            episode.script_finished_at = timezone.now()
            episode.save(
                update_fields=[
                    "full_script",
                    "script_status",
                    "script_error",
                    "pacing_payload",
                    "script_finished_at",
                    "updated_at",
                ]
            )
            if episode_number == 1:
                episode.script.episode_1_script = full_script
                episode.script.save(update_fields=["episode_1_script", "updated_at"])
            StoryboardPrompt.objects.filter(episode=episode).delete()
        return self.get_episode(workspace_id, episode_number, script_id=script_id)

    def mark_episode_script_generation_failed(
        self, workspace_id, episode_number, error, script_id=None
    ):
        with transaction.atomic():
            episode = self._get_episode_model(
                workspace_id,
                episode_number,
                for_update=True,
                script_id=script_id,
            )
            episode.script_status = Episode.SCRIPT_FAILED
            episode.script_error = str(error)
            episode.script_finished_at = timezone.now()
            episode.save(
                update_fields=["script_status", "script_error", "script_finished_at", "updated_at"]
            )

    def create_storyboard_task(self, workspace_id, episode_number=1, script_id=None):
        with transaction.atomic():
            try:
                project = Project.objects.select_for_update().get(workspace_id=workspace_id)
            except Project.DoesNotExist as exc:
                raise FileNotFoundError(f"Workspace not found: {workspace_id}") from exc
            episode = self._get_episode_model(workspace_id, episode_number, script_id=script_id)
            target_id = self._storyboard_task_target(script_id, episode_number)
            if not episode.full_script.strip():
                raise ValueError(f"请先生成第 {episode_number} 集完整剧本，再生成分镜。")
            running_task = project.generation_tasks.filter(
                task_type=GenerationTask.TYPE_STORYBOARD,
                target_id=target_id,
                status__in=[
                    GenerationTask.STATUS_PENDING,
                    GenerationTask.STATUS_RUNNING,
                    GenerationTask.STATUS_RETRY_WAIT,
                ],
            ).order_by("-created_at", "-id").first()
            if running_task:
                task_data = self._task_to_dict(running_task)
                task_data["created"] = False
                return task_data
            task = GenerationTask.objects.create(
                project=project,
                task_type=GenerationTask.TYPE_STORYBOARD,
                target_id=target_id,
                input_snapshot={
                    "episode_number": episode_number,
                    "script_id": script_id,
                },
            )
            task_data = self._task_to_dict(task)
            task_data["created"] = True
            return task_data

    def save_storyboard_for_episode(self, workspace_id, episode_number, prompts, script_id=None):
        with transaction.atomic():
            episode = self._get_episode_model(workspace_id, episode_number, for_update=True, script_id=script_id)
            StoryboardPrompt.objects.update_or_create(
                episode=episode,
                defaults={
                    "project": episode.script.project,
                    "script": episode.script,
                    "prompts_payload": prompts,
                },
            )
        return self.get_episode_workspace(workspace_id, episode_number, script_id=script_id)

    def start_task(self, task_id):
        with transaction.atomic():
            task = GenerationTask.objects.select_for_update().get(pk=task_id)
            task.status = GenerationTask.STATUS_RUNNING
            task.error_message = ""
            task.started_at = timezone.now()
            task.finished_at = None
            task.save(update_fields=["status", "error_message", "started_at", "finished_at"])
            return self._task_to_dict(task)

    def finish_task(self, task_id, result=None):
        with transaction.atomic():
            task = GenerationTask.objects.select_for_update().get(pk=task_id)
            task.status = GenerationTask.STATUS_SUCCEEDED
            task.result_snapshot = result or {}
            task.error_message = ""
            task.finished_at = timezone.now()
            task.save(update_fields=["status", "result_snapshot", "error_message", "finished_at"])
            return self._task_to_dict(task)

    def fail_task(self, task_id, error):
        with transaction.atomic():
            task = GenerationTask.objects.select_for_update().get(pk=task_id)
            task.status = GenerationTask.STATUS_FAILED
            task.error_message = str(error)
            task.finished_at = timezone.now()
            task.save(update_fields=["status", "error_message", "finished_at"])
            return self._task_to_dict(task)

    def get_task(self, task_id):
        try:
            task = GenerationTask.objects.select_related("project").get(pk=task_id)
        except GenerationTask.DoesNotExist as exc:
            raise FileNotFoundError(f"Generation task not found: {task_id}") from exc
        return self._task_to_dict(task)

    def latest_task(self, workspace_id, task_type):
        try:
            project = Project.objects.get(workspace_id=workspace_id)
        except Project.DoesNotExist as exc:
            raise FileNotFoundError(f"Workspace not found: {workspace_id}") from exc
        task = project.generation_tasks.filter(task_type=task_type).order_by("-created_at", "-id").first()
        return self._task_to_dict(task) if task else None

    def create_character_profile_task(self, workspace_id, visual_style=None, script_id=None):
        with transaction.atomic():
            project = Project.objects.select_for_update().get(workspace_id=workspace_id)
            script = self._script_for_context(project, script_id)
            if script is None:
                raise ValueError("请先生成剧集规划，再生成角色设定。")
            selected_style = visual_style or script.character_visual_style
            valid_styles = {value for value, _ in Script.CHARACTER_STYLE_CHOICES}
            if selected_style not in valid_styles:
                raise ValueError(f"Unsupported character visual style: {selected_style}")
            target_id = f"script:{script.id}"
            running = project.generation_tasks.filter(
                task_type=GenerationTask.TYPE_CHARACTER_PROFILE,
                target_id=target_id,
                status__in=[
                    GenerationTask.STATUS_PENDING,
                    GenerationTask.STATUS_RUNNING,
                    GenerationTask.STATUS_RETRY_WAIT,
                ],
            ).first()
            if running:
                data = self._task_to_dict(running)
                data["created"] = False
                return data
            if script.character_visual_style != selected_style:
                script.character_visual_style = selected_style
                script.save(update_fields=["character_visual_style", "updated_at"])
            task = GenerationTask.objects.create(
                project=project,
                task_type=GenerationTask.TYPE_CHARACTER_PROFILE,
                target_id=target_id,
                input_snapshot={
                    "script_id": script.id,
                    "project_id": script.outline_id,
                    "visual_style": selected_style,
                },
            )
            data = self._task_to_dict(task)
            data["created"] = True
            return data

    def save_character_profiles(self, workspace_id, profiles, script_id=None):
        with transaction.atomic():
            project = Project.objects.select_for_update().get(workspace_id=workspace_id)
            script = self._script_for_context(project, script_id)
            if script is None:
                raise ValueError("Script is required")
            names = []
            for position, profile in enumerate(profiles, start=1):
                if script.characters.filter(name=profile["name"], is_deleted=True).exists():
                    continue
                names.append(profile["name"])
                Character.objects.update_or_create(
                    script=script,
                    name=profile["name"],
                    defaults={
                        "role": profile["role"],
                        "appearance": profile["appearance"],
                        "personality": profile["personality"],
                        "costume": profile["costume"],
                        "image_prompt": profile["image_prompt"],
                        "position": position,
                    },
                )
            names.extend(script.characters.filter(is_deleted=True).values_list("name", flat=True))
            script.characters.exclude(name__in=names).filter(assets__isnull=True).delete()
        return self.get_workspace(workspace_id, script_id=script_id)

    def create_character_image_task(self, workspace_id, character_id, prompt):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("角色图片 Prompt 不能为空。")
        with transaction.atomic():
            project = Project.objects.select_for_update().get(workspace_id=workspace_id)
            character = Character.objects.select_for_update().get(
                pk=character_id,
                script__project=project,
            )
            if character.is_deleted:
                raise ValueError("Deleted characters cannot generate new images.")
            character.image_prompt = prompt.strip()
            character.save(update_fields=["image_prompt", "updated_at"])
            running = project.generation_tasks.filter(
                task_type=GenerationTask.TYPE_CHARACTER_IMAGE,
                target_id=str(character.id),
                status__in=[
                    GenerationTask.STATUS_PENDING,
                    GenerationTask.STATUS_RUNNING,
                    GenerationTask.STATUS_RETRY_WAIT,
                ],
            ).first()
            if running:
                data = self._task_to_dict(running)
                data["created"] = False
                return data
            task = GenerationTask.objects.create(
                project=project,
                task_type=GenerationTask.TYPE_CHARACTER_IMAGE,
                target_id=str(character.id),
                input_snapshot={
                    "script_id": character.script_id,
                    "project_id": character.script.outline_id,
                    "prompt": character.image_prompt,
                    "character_name": character.name,
                    "visual_style": character.script.character_visual_style,
                },
            )
            data = self._task_to_dict(task)
            data["created"] = True
            return data

    def get_character(self, workspace_id, character_id):
        try:
            character = Character.objects.select_related("script__project").prefetch_related("assets").get(
                pk=character_id,
                script__project__workspace_id=workspace_id,
            )
            if character.is_deleted:
                raise Character.DoesNotExist
        except Character.DoesNotExist as exc:
            raise FileNotFoundError(f"Character not found: {character_id}") from exc
        return self._character_to_dict(character)

    def save_character_asset(self, workspace_id, character_id, result, prompt_snapshot=None):
        with transaction.atomic():
            character = Character.objects.select_for_update().get(
                pk=character_id,
                script__project__workspace_id=workspace_id,
            )
            if character.is_deleted:
                raise Character.DoesNotExist
            version = (character.assets.aggregate(value=Max("version"))["value"] or 0) + 1
            asset = CharacterAsset(
                character=character,
                model=result.model,
                prompt_snapshot=prompt_snapshot or character.image_prompt,
                source_url=result.source_url,
                version=version,
            )
            filename = f"{uuid.uuid4().hex}{result.extension}"
            asset.image.save(filename, ContentFile(result.content), save=False)
            asset.save()
            from studio.services.video import bind_character_to_named_shots

            bind_character_to_named_shots(character, asset)
        return self.get_character(workspace_id, character_id)

    def get_latest_character_asset(self, workspace_id, character_id):
        asset = (
            CharacterAsset.objects.select_related("character__script__project")
            .filter(
                character_id=character_id,
                character__script__project__workspace_id=workspace_id,
            )
            .first()
        )
        if asset is None or not asset.image:
            raise FileNotFoundError(f"Character image not found: {character_id}")

        suffix = Path(asset.image.name).suffix
        asset.image.open("rb")
        return {
            "file": asset.image,
            "filename": f"{asset.character.name}-v{asset.version}{suffix}",
        }

    def mark_outline_usable(self, workspace_id, outline_id):
        with transaction.atomic():
            try:
                outline = Outline.objects.select_for_update().select_related("project").get(
                    project__workspace_id=workspace_id,
                    outline_id=outline_id,
                )
            except Outline.DoesNotExist as exc:
                raise FileNotFoundError(f"Outline not found: {outline_id}") from exc

            outline.is_usable = True
            if outline.usable_at is None:
                outline.usable_at = timezone.now()
            outline.save(update_fields=["is_usable", "usable_at"])
            workspace_id = outline.project.workspace_id

        return self.get_workspace(workspace_id)

    def outline_exists(self, workspace_id, outline_id):
        return Outline.objects.filter(
            project__workspace_id=workspace_id,
            outline_id=outline_id,
        ).exists()

    def list_usable_outlines(self, workspace_id=CURRENT_WORKSPACE_ID):
        try:
            project = Project.objects.get(workspace_id=workspace_id)
        except Project.DoesNotExist as exc:
            raise FileNotFoundError(f"Workspace not found: {workspace_id}") from exc
        outlines = (
            project.outlines.filter(is_usable=True)
            .select_related("script")
            .order_by("-usable_at", "-id")
        )
        return {
            "id": project.workspace_id,
            "genre": project.genre,
            "episode_count": project.episode_count,
            "episode_duration_minutes": project.episode_duration_minutes,
            "usable_outlines": [self._outline_to_dict(outline) for outline in outlines],
            "episode_duration_label": EPISODE_DURATION_LABEL,
        }

    def _create_outlines(self, project, outlines, batch_id=None):
        batch_id = batch_id or uuid.uuid4().hex[:12]
        used_ids = set(project.outlines.values_list("outline_id", flat=True))
        instances = []
        for index, outline in enumerate(outlines, start=1):
            outline_id = self._make_unique_outline_id(outline["id"], batch_id, used_ids)
            used_ids.add(outline_id)
            payload = dict(outline)
            payload["source_id"] = outline["id"]
            payload["id"] = outline_id
            instances.append(
                Outline(
                    project=project,
                    outline_id=outline_id,
                    batch_id=batch_id,
                    position=index,
                    title=outline["title"],
                    core_premise=outline["core_premise"],
                    protagonist=outline["protagonist"],
                    hook=outline["hook"],
                    arc_summary=outline["arc_summary"],
                    raw_payload=payload,
                )
            )
        Outline.objects.bulk_create(instances)

    @staticmethod
    def _make_unique_outline_id(base_id, batch_id, used_ids):
        if base_id not in used_ids:
            return base_id
        candidate = f"{batch_id}-{base_id}"[:120]
        suffix = 2
        while candidate in used_ids:
            suffix_text = f"-{suffix}"
            candidate = f"{batch_id}-{base_id}"[: 120 - len(suffix_text)] + suffix_text
            suffix += 1
        return candidate

    def _workspace_to_dict(self, project, script_override=None, outline_override=None):
        active_outlines = project.outlines.filter(is_archived=False).order_by("position", "id")
        usable_outlines = project.outlines.filter(is_usable=True).order_by("-usable_at", "-id")
        selected_outline = outline_override or (
            script_override.outline if script_override else project.selected_outline
        )
        script = script_override
        if script is None and selected_outline is not None:
            script = getattr(selected_outline, "script", None)
        episodes = list(script.episodes.select_related("storyboard_prompt").order_by("episode_number")) if script else []
        characters = list(script.characters.prefetch_related("assets")) if script else []
        characters = [character for character in characters if not character.is_deleted]
        script_choices = list(
            project.scripts.select_related("outline").order_by("-updated_at", "-id")
        )
        episode_1 = next(
            (episode for episode in episodes if episode.episode_number == 1),
            None,
        )
        storyboard_prompt = getattr(episode_1, "storyboard_prompt", None) if episode_1 else None
        return {
            "id": project.workspace_id,
            "genre": project.genre,
            "episode_count": project.episode_count,
            "episode_duration_minutes": project.episode_duration_minutes,
            "outlines": [self._outline_to_dict(outline) for outline in active_outlines],
            "usable_outlines": [self._outline_to_dict(outline) for outline in usable_outlines],
            "episode_duration_label": EPISODE_DURATION_LABEL,
            "selected_outline_id": (
                selected_outline.outline_id if selected_outline else None
            ),
            "project_id": selected_outline.pk if selected_outline else None,
            "selected_outline": (
                self._outline_to_dict(selected_outline)
                if selected_outline
                else None
            ),
            "script_id": script.id if script else None,
            "script_title": script.outline.title if script else "",
            "script_task": self._latest_task_dict(
                project,
                GenerationTask.TYPE_SCRIPT,
                target_id=selected_outline.outline_id,
            ) if selected_outline else None,
            "script_choices": [
                {
                    "id": item.id,
                    "project_id": item.outline_id,
                    "title": item.outline.title,
                }
                for item in script_choices
            ],
            "script_plan": script.plan_payload if script else [],
            "episodes": [self._episode_to_dict(episode) for episode in episodes],
            "characters": [self._character_to_dict(character) for character in characters],
            "character_visual_style": (
                script.character_visual_style if script else Script.CHARACTER_STYLE_COMIC
            ),
            "character_profile_task": self._latest_task_dict(
                project,
                GenerationTask.TYPE_CHARACTER_PROFILE,
                target_id=f"script:{script.id}" if script else "",
            ) if script else None,
            "episode_1_script": episode_1.full_script if episode_1 else "",
            "episode_1_pacing": episode_1.pacing_payload if episode_1 else {},
            "storyboard_prompts": (
                storyboard_prompt.prompts_payload if storyboard_prompt else []
            ),
            "storyboard_task": self._latest_task_dict(
                project,
                GenerationTask.TYPE_STORYBOARD,
                target_id="1",
            ),
            "created_at": self._format_datetime(project.created_at),
            "updated_at": self._format_datetime(project.updated_at),
        }

    def _outline_to_dict(self, outline):
        payload = dict(outline.raw_payload or {})
        payload.update(
            {
                "id": outline.outline_id,
                "project_id": outline.pk,
                "source_id": payload.get("source_id", outline.outline_id),
                "title": outline.title,
                "core_premise": outline.core_premise,
                "protagonist": outline.protagonist,
                "hook": outline.hook,
                "arc_summary": outline.arc_summary,
                "batch_id": outline.batch_id,
                "position": outline.position,
                "is_usable": outline.is_usable,
                "usable_at": self._format_datetime(outline.usable_at) if outline.usable_at else "",
                "is_archived": outline.is_archived,
                "has_script": self._outline_has_script(outline),
                "script_status": outline.script_status,
                "script_error": outline.script_error,
                "script_started_at": (
                    self._format_datetime(outline.script_started_at)
                    if outline.script_started_at
                    else ""
                ),
                "script_finished_at": (
                    self._format_datetime(outline.script_finished_at)
                    if outline.script_finished_at
                    else ""
                ),
            }
        )
        return payload

    @staticmethod
    def _outline_has_script(outline):
        return getattr(outline, "script", None) is not None

    @staticmethod
    def _script_for_selected_outline(project):
        if project.selected_outline_id is None:
            return None
        return getattr(project.selected_outline, "script", None)

    def _script_for_context(self, project, script_id=None):
        if script_id is None:
            return self._script_for_selected_outline(project)
        try:
            return Script.objects.get(pk=script_id, project=project)
        except Script.DoesNotExist as exc:
            raise FileNotFoundError(
                f"Script not found: {script_id}"
            ) from exc

    def _sync_episodes(
        self,
        script,
        script_plan,
        episode_1_script,
        episode_1_pacing=None,
    ):
        if not isinstance(script_plan, list):
            return
        if not script_plan and isinstance(episode_1_script, str) and episode_1_script.strip():
            script_plan = [
                {
                    "episode": 1,
                    "title": "第 1 集",
                    "summary": "由现有完整剧本创建",
                    "key_conflict": "待补充",
                    "cliffhanger": "待补充",
                }
            ]
        episode_numbers = []
        for position, item in enumerate(script_plan, start=1):
            if not isinstance(item, dict):
                continue
            episode_number = item.get("episode", position)
            if not isinstance(episode_number, int) or isinstance(episode_number, bool):
                episode_number = position
            episode_numbers.append(episode_number)
            episode, _ = Episode.objects.get_or_create(
                script=script,
                episode_number=episode_number,
                defaults={
                    "title": str(item.get("title") or f"第 {episode_number} 集"),
                    "summary": str(item.get("summary") or ""),
                    "key_conflict": str(item.get("key_conflict") or ""),
                    "cliffhanger": str(item.get("cliffhanger") or ""),
                },
            )
            episode.title = str(item.get("title") or f"第 {episode_number} 集")
            episode.summary = str(item.get("summary") or "")
            episode.key_conflict = str(item.get("key_conflict") or "")
            episode.cliffhanger = str(item.get("cliffhanger") or "")
            episode.plan_payload = item
            if episode_number == 1 and isinstance(episode_1_script, str):
                episode.full_script = episode_1_script
                episode.script_status = (
                    Episode.SCRIPT_READY if episode_1_script.strip() else Episode.SCRIPT_PENDING
                )
                if isinstance(episode_1_pacing, dict):
                    episode.pacing_payload = episode_1_pacing
                episode.script_error = ""
            episode.save()

        if episode_numbers:
            script.episodes.exclude(episode_number__in=episode_numbers).delete()

    def _get_episode_model(self, workspace_id, episode_number, for_update=False, script_id=None):
        if not isinstance(episode_number, int) or isinstance(episode_number, bool):
            raise ValueError("Episode number must be an integer")
        try:
            project = Project.objects.only("selected_outline_id").get(workspace_id=workspace_id)
        except Project.DoesNotExist as exc:
            raise FileNotFoundError(f"Workspace not found: {workspace_id}") from exc
        if script_id is None and project.selected_outline_id is None:
            raise ValueError("请先选择一个大纲并生成剧集规划。")

        queryset = Episode.objects.select_related("script__project", "script__outline", "storyboard_prompt")
        if for_update:
            queryset = queryset.select_for_update()
        try:
            filters = {"episode_number": episode_number, "script__project": project}
            if script_id is not None:
                filters["script_id"] = script_id
            else:
                filters["script__outline_id"] = project.selected_outline_id
            return queryset.get(**filters)
        except Episode.DoesNotExist as exc:
            raise FileNotFoundError(f"Episode not found: {episode_number}") from exc

    def _episode_to_dict(self, episode):
        storyboard = getattr(episode, "storyboard_prompt", None)
        return {
            "episode": episode.episode_number,
            "title": episode.title,
            "summary": episode.summary,
            "key_conflict": episode.key_conflict,
            "cliffhanger": episode.cliffhanger,
            "full_script": episode.full_script,
            "has_script": bool(episode.full_script.strip()),
            "script_status": episode.script_status,
            "script_error": episode.script_error,
            "episode_goal": episode.plan_payload.get("episode_goal", ""),
            "obstacle_1": episode.plan_payload.get("obstacle_1", ""),
            "obstacle_2": episode.plan_payload.get("obstacle_2", ""),
            "resolution_or_reversal": episode.plan_payload.get("resolution_or_reversal", ""),
            "next_crisis": episode.plan_payload.get("next_crisis", ""),
            "plan_payload": episode.plan_payload,
            "pacing_payload": episode.pacing_payload,
            "has_storyboard": storyboard is not None,
            "storyboard_count": len(storyboard.prompts_payload) if storyboard else 0,
            "script_started_at": (
                self._format_datetime(episode.script_started_at)
                if episode.script_started_at
                else ""
            ),
            "script_finished_at": (
                self._format_datetime(episode.script_finished_at)
                if episode.script_finished_at
                else ""
            ),
        }

    def _character_to_dict(self, character):
        assets = list(character.assets.all())
        latest_asset = assets[0] if assets else None
        project = character.script.project
        return {
            "id": character.id,
            "name": character.name,
            "role": character.role,
            "appearance": character.appearance,
            "personality": character.personality,
            "costume": character.costume,
            "image_prompt": character.image_prompt,
            "visual_style": character.script.character_visual_style,
            "image_url": latest_asset.image.url if latest_asset else "",
            "image_model": latest_asset.model if latest_asset else "",
            "version": latest_asset.version if latest_asset else 0,
            "image_task": self._latest_task_dict(
                project,
                GenerationTask.TYPE_CHARACTER_IMAGE,
                target_id=str(character.id),
            ),
        }

    def _latest_task_dict(self, project, task_type, target_id=None):

        tasks = project.generation_tasks.filter(task_type=task_type)
        if target_id is not None:
            tasks = tasks.filter(target_id=str(target_id))
        task = tasks.order_by("-created_at", "-id").first()
        return self._task_to_dict(task) if task else None

    def _task_to_dict(self, task):
        if task is None:
            return None
        stage_map = {
            task.STATUS_PENDING: ("created", "生成任务已创建", "任务已进入队列，正在等待执行。"),
            task.STATUS_RUNNING: ("model", "等待大模型返回", "请求已发送，正在等待大模型生成结果。"),
            task.STATUS_RETRY_WAIT: ("model", "等待重试", "模型服务暂时不可用，系统会自动重试。"),
            task.STATUS_SUCCEEDED: ("result", "结果处理完成", "模型结果已返回并保存。"),
            task.STATUS_FAILED: (
                "failed",
                "生成任务失败",
                task.error_message or "生成过程中发生未知错误，请重试。",
            ),
            task.STATUS_CANCELLED: (
                "failed",
                "生成任务已取消",
                task.error_message or "任务已取消。",
            ),
        }
        stage, stage_label, stage_detail = stage_map.get(
            task.status,
            ("created", "正在处理任务", "正在获取最新任务状态。"),
        )
        return {
            "id": task.id,
            "task_type": task.task_type,
            "status": task.status,
            "workspace_id": task.project.workspace_id,
            "target_id": task.target_id,
            "input_snapshot": task.input_snapshot,
            "result_snapshot": task.result_snapshot,
            "error_message": task.error_message,
            "error_code": task.error_code,
            "error_details": task.error_details,
            "progress_current": task.progress_current,
            "progress_total": task.progress_total,
            "progress_percent": task.progress_percent,
            "attempt_count": task.attempt_count,
            "max_attempts": task.max_attempts,
            "next_retry_at": (
                self._format_datetime(task.next_retry_at) if task.next_retry_at else ""
            ),
            "can_retry": task.status in {task.STATUS_FAILED, task.STATUS_CANCELLED},
            "stage": stage,
            "stage_label": stage_label,
            "stage_detail": stage_detail,
            "created_at": self._format_datetime(task.created_at),
            "started_at": self._format_datetime(task.started_at) if task.started_at else "",
            "finished_at": self._format_datetime(task.finished_at) if task.finished_at else "",
        }

    @staticmethod
    def _format_datetime(value):
        return timezone.localtime(value).isoformat(timespec="seconds")


    def delete_character(self, workspace_id, character_id, script_id=None):
        from studio.models import ShotCharacterReference

        with transaction.atomic():
            project = Project.objects.select_for_update().get(workspace_id=workspace_id)
            try:
                character = Character.objects.select_for_update().get(
                    pk=character_id,
                    script__project=project,
                    is_deleted=False,
                )
            except Character.DoesNotExist as exc:
                raise FileNotFoundError(f"Character not found: {character_id}") from exc
            if script_id and character.script_id != script_id:
                raise FileNotFoundError(f"Character not found: {character_id}")
            ShotCharacterReference.objects.filter(character=character).delete()
            project.generation_tasks.filter(
                task_type=GenerationTask.TYPE_CHARACTER_IMAGE,
                target_id=str(character.id),
                status__in=[
                    GenerationTask.STATUS_PENDING,
                    GenerationTask.STATUS_RUNNING,
                    GenerationTask.STATUS_RETRY_WAIT,
                ],
            ).update(
                status=GenerationTask.STATUS_CANCELLED,
                error_message="Character deleted; generation task cancelled.",
                finished_at=timezone.now(),
                next_retry_at=None,
                lease_owner="",
                lease_expires_at=None,
            )
            character.is_deleted = True
            character.deleted_at = timezone.now()
            character.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
        return character
JsonWorkspaceRepository = WorkspaceRepository
