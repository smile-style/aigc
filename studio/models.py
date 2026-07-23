import uuid

from django.db import models


class Project(models.Model):
    workspace_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    genre = models.CharField(max_length=120)
    episode_count = models.PositiveIntegerField()
    episode_duration_minutes = models.PositiveIntegerField()
    selected_outline = models.ForeignKey(
        "Outline",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.name


class Outline(models.Model):
    SCRIPT_PENDING = "pending"
    SCRIPT_GENERATING = "generating"
    SCRIPT_READY = "ready"
    SCRIPT_FAILED = "failed"
    SCRIPT_STATUS_CHOICES = [
        (SCRIPT_PENDING, "Pending"),
        (SCRIPT_GENERATING, "Generating"),
        (SCRIPT_READY, "Ready"),
        (SCRIPT_FAILED, "Failed"),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="outlines")
    outline_id = models.CharField(max_length=120)
    batch_id = models.CharField(max_length=64, blank=True, default="")
    position = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    core_premise = models.TextField()
    protagonist = models.TextField()
    hook = models.TextField()
    arc_summary = models.TextField()
    raw_payload = models.JSONField(default=dict)
    is_usable = models.BooleanField(default=False, db_index=True)
    usable_at = models.DateTimeField(null=True, blank=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    script_status = models.CharField(
        max_length=20,
        choices=SCRIPT_STATUS_CHOICES,
        default=SCRIPT_PENDING,
        db_index=True,
    )
    script_error = models.TextField(blank=True)
    script_started_at = models.DateTimeField(null=True, blank=True)
    script_finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["is_archived", "position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "outline_id"],
                name="unique_outline_id_per_project",
            ),
        ]

    def __str__(self):
        return self.title


class Script(models.Model):
    CHARACTER_STYLE_COMIC = "comic"
    CHARACTER_STYLE_REALISTIC = "realistic"
    CHARACTER_STYLE_CHOICES = [
        (CHARACTER_STYLE_COMIC, "Comic"),
        (CHARACTER_STYLE_REALISTIC, "Realistic"),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="scripts")
    outline = models.OneToOneField(Outline, on_delete=models.CASCADE, related_name="script")
    plan_payload = models.JSONField(default=list)
    episode_1_script = models.TextField(blank=True)
    raw_payload = models.JSONField(default=dict)
    character_visual_style = models.CharField(
        max_length=20,
        choices=CHARACTER_STYLE_CHOICES,
        default=CHARACTER_STYLE_COMIC,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Script for {self.outline}"


class Episode(models.Model):
    SCRIPT_PENDING = "pending"
    SCRIPT_GENERATING = "generating"
    SCRIPT_READY = "ready"
    SCRIPT_FAILED = "failed"
    SCRIPT_STATUS_CHOICES = [
        (SCRIPT_PENDING, "Pending"),
        (SCRIPT_GENERATING, "Generating"),
        (SCRIPT_READY, "Ready"),
        (SCRIPT_FAILED, "Failed"),
    ]

    script = models.ForeignKey(Script, on_delete=models.CASCADE, related_name="episodes")
    episode_number = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    summary = models.TextField()
    key_conflict = models.TextField()
    cliffhanger = models.TextField()
    full_script = models.TextField(blank=True)
    plan_payload = models.JSONField(default=dict, blank=True)
    pacing_payload = models.JSONField(default=dict, blank=True)
    script_status = models.CharField(
        max_length=20,
        choices=SCRIPT_STATUS_CHOICES,
        default=SCRIPT_PENDING,
        db_index=True,
    )
    script_error = models.TextField(blank=True)
    script_started_at = models.DateTimeField(null=True, blank=True)
    script_finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["episode_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["script", "episode_number"],
                name="unique_episode_number_per_script",
            ),
        ]

    def __str__(self):
        return f"Episode {self.episode_number} of {self.script}"


class StoryboardPrompt(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="storyboard_prompts",
    )
    script = models.ForeignKey(
        Script,
        on_delete=models.CASCADE,
        related_name="storyboard_prompts",
    )
    episode = models.OneToOneField(
        Episode,
        on_delete=models.CASCADE,
        related_name="storyboard_prompt",
    )
    prompts_payload = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Storyboard prompts for episode {self.episode.episode_number}"


class Character(models.Model):
    script = models.ForeignKey(Script, on_delete=models.CASCADE, related_name="characters")
    name = models.CharField(max_length=120)
    role = models.CharField(max_length=255)
    appearance = models.TextField()
    personality = models.TextField(blank=True)
    costume = models.TextField(blank=True)
    image_prompt = models.TextField()
    position = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["script", "name"],
                name="unique_character_name_per_script",
            ),
        ]

    def __str__(self):
        return self.name


class CharacterAsset(models.Model):
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="assets")
    model = models.CharField(max_length=120)
    prompt_snapshot = models.TextField()
    image = models.FileField(upload_to="characters/%Y/%m/%d")
    source_url = models.URLField(blank=True, max_length=1000)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["character", "version"],
                name="unique_character_asset_version",
            ),
        ]

    def __str__(self):
        return f"{self.character} v{self.version}"


class CoverTemplate(models.Model):
    script = models.OneToOneField(
        Script, on_delete=models.CASCADE, related_name="cover_template"
    )
    prompt_snapshot = models.TextField()
    background = models.FileField(upload_to="covers/templates/%Y/%m/%d")
    source_url = models.URLField(blank=True, max_length=1000)
    model = models.CharField(max_length=120)
    style_payload = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Cover template for {self.script} v{self.version}"


class EpisodeCover(models.Model):
    template = models.ForeignKey(
        CoverTemplate, on_delete=models.CASCADE, related_name="episode_covers"
    )
    episode = models.OneToOneField(
        Episode, on_delete=models.CASCADE, related_name="cover"
    )
    title = models.CharField(max_length=20)
    title_customized = models.BooleanField(default=False)
    image = models.FileField(upload_to="covers/episodes/%Y/%m/%d")
    template_version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["episode__episode_number"]


class GenerationTask(models.Model):
    TYPE_OUTLINE = "outline"
    TYPE_SCRIPT = "script"
    TYPE_EPISODE_SCRIPT = "episode_script"
    TYPE_STORYBOARD = "storyboard"
    TYPE_CHARACTER_PROFILE = "character_profile"
    TYPE_CHARACTER_IMAGE = "character_image"
    TYPE_COVER_IMAGE = "cover_image"
    TYPE_CHOICES = [
        (TYPE_OUTLINE, "Outline"),
        (TYPE_SCRIPT, "Script"),
        (TYPE_EPISODE_SCRIPT, "Episode script"),
        (TYPE_STORYBOARD, "Storyboard"),
        (TYPE_CHARACTER_PROFILE, "Character profile"),
        (TYPE_CHARACTER_IMAGE, "Character image"),
        (TYPE_COVER_IMAGE, "Cover image"),
    ]

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_RETRY_WAIT = "retry_wait"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_RETRY_WAIT, "Retry wait"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="generation_tasks")
    task_type = models.CharField(max_length=32, choices=TYPE_CHOICES, db_index=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    target_id = models.CharField(max_length=120, blank=True, default="")
    input_snapshot = models.JSONField(default=dict, blank=True)
    result_snapshot = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_details = models.JSONField(default=dict, blank=True)
    progress_current = models.PositiveIntegerField(default=0)
    progress_total = models.PositiveIntegerField(default=0)
    progress_percent = models.PositiveSmallIntegerField(default=0)
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    idempotency_key = models.CharField(max_length=160, blank=True, default="", db_index=True)
    priority = models.SmallIntegerField(default=0, db_index=True)
    lease_owner = models.CharField(max_length=160, blank=True, default="")
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["status", "next_retry_at", "priority", "created_at"],
                name="generation_task_queue_idx",
            ),
        ]

    def __str__(self):
        return f"{self.task_type}:{self.status} for {self.project}"


class GenerationAttempt(models.Model):
    task = models.ForeignKey(
        GenerationTask,
        on_delete=models.CASCADE,
        related_name="attempts",
    )
    attempt_number = models.PositiveIntegerField()
    worker_id = models.CharField(max_length=160, blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    succeeded = models.BooleanField(default=False)
    retryable = models.BooleanField(default=False)
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["attempt_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["task", "attempt_number"],
                name="unique_generation_attempt_number",
            ),
        ]


# Imported after the core models to avoid circular references.
from .video_models import *  # noqa: E402,F401,F403
