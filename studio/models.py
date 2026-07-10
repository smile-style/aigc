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
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="scripts")
    outline = models.OneToOneField(Outline, on_delete=models.CASCADE, related_name="script")
    plan_payload = models.JSONField(default=list)
    episode_1_script = models.TextField(blank=True)
    raw_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Script for {self.outline}"


class StoryboardPrompt(models.Model):
    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name="storyboard_prompt",
    )
    script = models.ForeignKey(
        Script,
        on_delete=models.CASCADE,
        related_name="storyboard_prompts",
    )
    prompts_payload = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Storyboard prompts for {self.project}"