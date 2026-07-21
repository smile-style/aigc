import uuid
from pathlib import Path

from django.db import models
from django.utils.text import slugify

from .models import Character, CharacterAsset, Episode, GenerationTask, StoryboardPrompt


def script_storage_key(script):
    title = slugify(script.outline.title, allow_unicode=True)[:80] or "script"
    return f"S{script.id:06d}-{title}"


def shot_video_upload_to(instance, filename):
    shot = instance.shot
    episode = shot.storyboard.episode
    suffix = Path(filename).suffix.lower() or ".mp4"
    return (
        f"videos/{script_storage_key(episode.script)}/"
        f"episode-{episode.episode_number:03d}/shots/"
        f"shot-{shot.shot_number:03d}/v{instance.version:03d}{suffix}"
    )


def composition_video_upload_to(instance, filename):
    episode = instance.episode
    suffix = Path(filename).suffix.lower() or ".mp4"
    return (
        f"videos/{script_storage_key(episode.script)}/"
        f"episode-{episode.episode_number:03d}/compositions/"
        f"v{instance.version:03d}{suffix}"
    )


class ProviderConfig(models.Model):
    TYPE_OPENAI_COMPATIBLE = "openai_compatible"
    TYPE_DASHSCOPE = "dashscope"
    TYPE_CHOICES = [
        (TYPE_OPENAI_COMPATIBLE, "OpenAI compatible"),
        (TYPE_DASHSCOPE, "Aliyun Bailian / DashScope"),
    ]

    name = models.CharField(max_length=120, unique=True)
    provider_type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    base_url = models.URLField(max_length=500)
    api_key_env_var = models.CharField(max_length=120, default="DASHSCOPE_API_KEY")
    enabled = models.BooleanField(default=True)
    timeout_seconds = models.PositiveIntegerField(default=600)
    max_retries = models.PositiveIntegerField(default=3)
    max_concurrency = models.PositiveIntegerField(default=2)
    api_key_ciphertext = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class ModelConfig(models.Model):
    VERIFICATION_UNTESTED = "untested"
    VERIFICATION_SUCCESS = "success"
    VERIFICATION_PARTIAL = "partial"
    VERIFICATION_FAILED = "failed"
    VERIFICATION_CHOICES = [
        (VERIFICATION_UNTESTED, "Untested"),
        (VERIFICATION_SUCCESS, "Verified"),
        (VERIFICATION_PARTIAL, "Partially verified"),
        (VERIFICATION_FAILED, "Failed"),
    ]
    CAPABILITY_TEXT = "text"
    CAPABILITY_IMAGE = "image"
    CAPABILITY_VIDEO_REFERENCE = "video_reference"
    CAPABILITY_VIDEO_IMAGE = "video_image"
    CAPABILITY_VIDEO_EDIT = "video_edit"
    CAPABILITY_CHOICES = [
        (CAPABILITY_TEXT, "Text generation"),
        (CAPABILITY_IMAGE, "Image generation"),
        (CAPABILITY_VIDEO_REFERENCE, "Reference-to-video"),
        (CAPABILITY_VIDEO_IMAGE, "Image-to-video"),
        (CAPABILITY_VIDEO_EDIT, "Video editing"),
    ]

    provider = models.ForeignKey(ProviderConfig, on_delete=models.PROTECT, related_name="models")
    name = models.CharField(max_length=120)
    model_id = models.CharField(max_length=160)
    capability = models.CharField(max_length=40, choices=CAPABILITY_CHOICES, db_index=True)
    default_parameters = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    verification_status = models.CharField(max_length=20, choices=VERIFICATION_CHOICES, default=VERIFICATION_UNTESTED)
    verification_message = models.CharField(max_length=500, blank=True)
    verification_latency_ms = models.PositiveIntegerField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["capability", "name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "model_id", "capability"],
                name="unique_provider_model_capability",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.model_id})"


class ModelAssignment(models.Model):
    PURPOSE_OUTLINE = "outline"
    PURPOSE_SCRIPT = "script"
    PURPOSE_EPISODE_SCRIPT = "episode_script"
    PURPOSE_CHARACTER_PROFILE = "character_profile"
    PURPOSE_STORYBOARD = "storyboard"
    PURPOSE_CHARACTER_IMAGE = "character_image"
    PURPOSE_SHOT_VIDEO = "shot_video"
    PURPOSE_SHOT_VIDEO_FALLBACK = "shot_video_fallback"
    PURPOSE_VIDEO_EDIT = "video_edit"
    PURPOSE_CHOICES = [
        (PURPOSE_OUTLINE, "大纲生成"),
        (PURPOSE_SCRIPT, "剧本规划"),
        (PURPOSE_EPISODE_SCRIPT, "分集剧本"),
        (PURPOSE_CHARACTER_PROFILE, "角色设定"),
        (PURPOSE_STORYBOARD, "分镜生成"),
        (PURPOSE_CHARACTER_IMAGE, "角色图片"),
        (PURPOSE_SHOT_VIDEO, "分镜视频主模型"),
        (PURPOSE_SHOT_VIDEO_FALLBACK, "分镜视频备用模型"),
        (PURPOSE_VIDEO_EDIT, "视频编辑"),
    ]

    purpose = models.CharField(max_length=48, choices=PURPOSE_CHOICES, unique=True)
    model = models.ForeignKey(ModelConfig, on_delete=models.PROTECT, related_name="assignments")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["purpose"]


class StoryboardShot(models.Model):
    storyboard = models.ForeignKey(StoryboardPrompt, on_delete=models.CASCADE, related_name="shots")
    shot_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    shot_number = models.PositiveIntegerField()
    position = models.PositiveIntegerField()
    duration_seconds = models.PositiveIntegerField(default=5)
    duration_label = models.CharField(max_length=60, blank=True)
    visual_description = models.TextField(blank=True)
    character_action = models.TextField(blank=True)
    dialogue_or_narration = models.TextField(blank=True)
    camera_language = models.TextField(blank=True)
    image_prompt = models.TextField(blank=True)
    video_prompt = models.TextField()
    video_prompt_override = models.TextField(blank=True)
    video_prompt_override_source_hash = models.CharField(max_length=64, blank=True, default="")
    negative_prompt = models.TextField(blank=True)
    character_names = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "shot_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["storyboard", "shot_number"],
                name="unique_storyboard_shot_number",
            )
        ]


class ShotCharacterReference(models.Model):
    shot = models.ForeignKey(StoryboardShot, on_delete=models.CASCADE, related_name="character_references")
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="shot_references")
    asset = models.ForeignKey(CharacterAsset, on_delete=models.PROTECT, related_name="shot_references")
    position = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["shot", "character"], name="unique_character_per_shot")
        ]


class VideoAsset(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_SUBMITTING = "submitting"
    STATUS_RUNNING = "running"
    STATUS_DOWNLOADING = "downloading"
    STATUS_READY = "ready"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_SUBMITTING, "Submitting"),
        (STATUS_RUNNING, "Running"),
        (STATUS_DOWNLOADING, "Downloading"),
        (STATUS_READY, "Ready"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_EXPIRED, "Expired"),
    ]

    shot = models.ForeignKey(StoryboardShot, on_delete=models.CASCADE, related_name="video_assets")
    generation_task = models.OneToOneField(
        GenerationTask, null=True, blank=True, on_delete=models.SET_NULL, related_name="video_asset"
    )
    model_config = models.ForeignKey(
        ModelConfig, null=True, blank=True, on_delete=models.PROTECT, related_name="video_assets"
    )
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_QUEUED, db_index=True)
    provider_task_id = models.CharField(max_length=160, blank=True, db_index=True)
    provider_request_id = models.CharField(max_length=160, blank=True)
    input_hash = models.CharField(max_length=64, blank=True, db_index=True)
    prompt_snapshot = models.TextField()
    input_snapshot = models.JSONField(default=dict, blank=True)
    result_snapshot = models.JSONField(default=dict, blank=True)
    source_url = models.URLField(blank=True, max_length=1500)
    video = models.FileField(upload_to=shot_video_upload_to, blank=True)
    error_message = models.TextField(blank=True)
    is_selected = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-version", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["shot", "version"], name="unique_shot_video_version")
        ]


class VideoComposition(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_EXPORTING = "exporting"
    STATUS_READY = "ready"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_EXPORTING, "Exporting"),
        (STATUS_READY, "Ready"),
        (STATUS_FAILED, "Failed"),
    ]

    episode = models.ForeignKey(
        Episode,
        on_delete=models.CASCADE,
        related_name="video_compositions",
    )
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    video = models.FileField(upload_to=composition_video_upload_to, blank=True)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    error_message = models.TextField(blank=True)
    exported_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-version", "-id"]
        indexes = [
            models.Index(
                fields=["status", "-exported_at"],
                name="video_comp_status_export_idx",
            )
        ]
        constraints = [
            models.UniqueConstraint(fields=["episode", "version"], name="unique_episode_export_version")
        ]


GenerationTask.TYPE_SHOT_VIDEO = "shot_video"
GenerationTask.TYPE_VIDEO_EXPORT = "video_export"

# Expose publishing models through studio.models while keeping a separate domain module.
from .publishing_models import *  # noqa: E402,F401,F403
