import uuid

from django.db import transaction
from django.utils import timezone

from studio.constants import EPISODE_COUNT, EPISODE_DURATION_MINUTES, GENRES
from studio.models import Outline, Project, Script, StoryboardPrompt

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

    def get_workspace(self, workspace_id):
        try:
            project = (
                Project.objects.select_related(
                    "selected_outline",
                    "selected_outline__script",
                    "storyboard_prompt",
                )
                .prefetch_related("outlines", "outlines__script")
                .get(workspace_id=workspace_id)
            )
        except Project.DoesNotExist as exc:
            raise FileNotFoundError(f"Workspace not found: {workspace_id}") from exc
        return self._workspace_to_dict(project)

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
                StoryboardPrompt.objects.update_or_create(
                    project=project,
                    defaults={
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

            return self._outline_to_dict(outline)

    def save_script_for_outline(self, workspace_id, outline_id, script_plan, episode_1_script):
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

    def _workspace_to_dict(self, project):
        active_outlines = project.outlines.filter(is_archived=False).order_by("position", "id")
        usable_outlines = project.outlines.filter(is_usable=True).order_by("-usable_at", "-id")
        script = self._script_for_selected_outline(project)
        storyboard_prompt = getattr(project, "storyboard_prompt", None)
        if storyboard_prompt and script and storyboard_prompt.script_id != script.id:
            storyboard_prompt = None
        if storyboard_prompt and script is None:
            storyboard_prompt = None
        return {
            "id": project.workspace_id,
            "genre": project.genre,
            "episode_count": project.episode_count,
            "episode_duration_minutes": project.episode_duration_minutes,
            "outlines": [self._outline_to_dict(outline) for outline in active_outlines],
            "usable_outlines": [self._outline_to_dict(outline) for outline in usable_outlines],
            "selected_outline_id": (
                project.selected_outline.outline_id if project.selected_outline_id else None
            ),
            "selected_outline": (
                self._outline_to_dict(project.selected_outline)
                if project.selected_outline_id
                else None
            ),
            "script_plan": script.plan_payload if script else [],
            "episode_1_script": script.episode_1_script if script else "",
            "storyboard_prompts": (
                storyboard_prompt.prompts_payload if storyboard_prompt else []
            ),
            "created_at": self._format_datetime(project.created_at),
            "updated_at": self._format_datetime(project.updated_at),
        }

    def _outline_to_dict(self, outline):
        payload = dict(outline.raw_payload or {})
        payload.update(
            {
                "id": outline.outline_id,
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

    @staticmethod
    def _format_datetime(value):
        return timezone.localtime(value).isoformat(timespec="seconds")


JsonWorkspaceRepository = WorkspaceRepository