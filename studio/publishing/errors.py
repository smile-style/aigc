class PublishingError(Exception):
    def __init__(self, message, *, code="publishing_error", retryable=False, details=None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details or {}


class PublishingValidationError(PublishingError):
    def __init__(self, message, *, code="invalid_submission", details=None):
        super().__init__(message, code=code, retryable=False, details=details)


class PublishingAuthError(PublishingError):
    def __init__(self, message="平台登录已失效，请重新登录。", *, details=None):
        super().__init__(message, code="authentication_expired", retryable=False, details=details)


class PublishingRetryableError(PublishingError):
    def __init__(self, message, *, code="temporary_platform_error", details=None):
        super().__init__(message, code=code, retryable=True, details=details)


class PublishingOutcomeUnknown(PublishingError):
    def __init__(self, message="平台可能已经收到稿件，但未返回明确结果。"):
        super().__init__(message, code="submission_outcome_unknown", retryable=False)


class PublishingCancelled(PublishingError):
    def __init__(self):
        super().__init__("发布任务已取消。", code="cancelled", retryable=False)
