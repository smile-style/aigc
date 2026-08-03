import uuid

from django.db import models


class PublishingAccount(models.Model):
    PLATFORM_BILIBILI = "bilibili"
    PLATFORM_ACFUN = "acfun"
    PLATFORM_DOUYIN = "douyin"
    PLATFORM_CHOICES = [
        (PLATFORM_BILIBILI, "Bilibili"),
        (PLATFORM_ACFUN, "AcFun"),
        (PLATFORM_DOUYIN, "抖音"),
    ]
    STATUS_CONNECTED = "connected"
    STATUS_EXPIRED = "expired"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_CONNECTED, "已连接"),
        (STATUS_EXPIRED, "登录已失效"),
        (STATUS_ERROR, "连接异常"),
    ]

    platform = models.CharField(max_length=32, choices=PLATFORM_CHOICES, db_index=True)
    remote_account_id = models.CharField(max_length=120, blank=True, db_index=True)
    display_name = models.CharField(max_length=160)
    credential_ciphertext = models.TextField()
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_CONNECTED, db_index=True)
    profile_snapshot = models.JSONField(default=dict, blank=True)
    platform_options = models.JSONField(default=dict, blank=True)
    max_concurrency = models.PositiveIntegerField(default=1)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["platform", "display_name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["platform", "remote_account_id"],
                name="unique_publishing_platform_account",
            )
        ]


class PublishingLoginSession(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SCANNED = "scanned"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_EXPIRED = "expired"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "等待扫码"),
        (STATUS_SCANNED, "已扫码，等待确认"),
        (STATUS_SUCCEEDED, "登录成功"),
        (STATUS_EXPIRED, "二维码已过期"),
        (STATUS_FAILED, "登录失败"),
    ]

    session_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    platform = models.CharField(max_length=32, choices=PublishingAccount.PLATFORM_CHOICES)
    provider_key_ciphertext = models.TextField()
    callback_state_hash = models.CharField(max_length=64, blank=True, db_index=True)
    login_url = models.URLField(max_length=2000)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_PENDING)
    account = models.ForeignKey(
        PublishingAccount,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="login_sessions",
    )
    error_message = models.TextField(blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class PublishingTask(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_RETRY_WAIT = "retry_wait"
    STATUS_SUBMITTED = "submitted"
    STATUS_PUBLISHED = "published"
    STATUS_REJECTED = "rejected"
    STATUS_OUTCOME_UNKNOWN = "outcome_unknown"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "等待发布"),
        (STATUS_RUNNING, "发布中"),
        (STATUS_RETRY_WAIT, "等待重试"),
        (STATUS_SUBMITTED, "已提交"),
        (STATUS_PUBLISHED, "已发布"),
        (STATUS_REJECTED, "平台退回"),
        (STATUS_OUTCOME_UNKNOWN, "平台结果待确认"),
        (STATUS_FAILED, "发布失败"),
        (STATUS_CANCELLED, "已取消"),
    ]

    STAGE_VALIDATING = "validating"
    STAGE_PREPARING = "preparing"
    STAGE_UPLOADING = "uploading"
    STAGE_SUBMITTING = "submitting"
    STAGE_PROCESSING = "processing"
    STAGE_RECONCILING = "reconciling"
    STAGE_CHOICES = [
        (STAGE_VALIDATING, "检查账号和稿件"),
        (STAGE_PREPARING, "准备上传"),
        (STAGE_UPLOADING, "上传成片"),
        (STAGE_SUBMITTING, "提交稿件"),
        (STAGE_PROCESSING, "等待平台处理"),
        (STAGE_RECONCILING, "核对平台结果"),
    ]

    composition = models.ForeignKey(
        "studio.VideoComposition", on_delete=models.PROTECT, related_name="publishing_tasks"
    )
    account = models.ForeignKey(PublishingAccount, on_delete=models.PROTECT, related_name="tasks")
    platform = models.CharField(max_length=32, choices=PublishingAccount.PLATFORM_CHOICES, db_index=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_QUEUED, db_index=True)
    stage = models.CharField(max_length=32, choices=STAGE_CHOICES, default=STAGE_VALIDATING)
    progress_percent = models.PositiveSmallIntegerField(default=0)
    uploaded_bytes = models.PositiveBigIntegerField(default=0)
    total_bytes = models.PositiveBigIntegerField(default=0)
    metadata_snapshot = models.JSONField(default=dict)
    content_hash = models.CharField(max_length=64, db_index=True)
    dedup_key = models.CharField(max_length=64, unique=True)
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    lease_owner = models.CharField(max_length=120, blank=True, db_index=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    cancel_requested = models.BooleanField(default=False)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.TextField(blank=True)
    error_details = models.JSONField(default=dict, blank=True)
    remote_video_id = models.CharField(max_length=160, blank=True, db_index=True)
    remote_url = models.URLField(max_length=2000, blank=True)
    remote_status = models.CharField(max_length=80, blank=True)
    remote_payload = models.JSONField(default=dict, blank=True)
    upload_snapshot = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True, db_index=True)
    acknowledged_by = models.CharField(max_length=120, blank=True)
    acknowledgement_note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "next_retry_at", "created_at"], name="publish_task_queue_idx"),
            models.Index(fields=["account", "status"], name="publish_account_status_idx"),
            models.Index(fields=["status", "acknowledged_at", "finished_at"], name="publish_task_history_idx"),
        ]

    @property
    def is_terminal(self):
        return self.status in {
            self.STATUS_PUBLISHED,
            self.STATUS_REJECTED,
            self.STATUS_FAILED,
            self.STATUS_CANCELLED,
        }


class PublishingAttempt(models.Model):
    task = models.ForeignKey(PublishingTask, on_delete=models.CASCADE, related_name="attempts")
    attempt_number = models.PositiveIntegerField()
    stage = models.CharField(max_length=32, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.TextField(blank=True)
    retryable = models.BooleanField(default=False)
    provider_response = models.JSONField(default=dict, blank=True)
    upload_session = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["attempt_number", "id"]
        constraints = [
            models.UniqueConstraint(fields=["task", "attempt_number"], name="unique_publish_attempt_number")
        ]
