from dataclasses import dataclass, field
from typing import Any


@dataclass
class LoginStart:
    provider_key: str
    login_url: str
    expires_in: int = 180


@dataclass
class LoginPoll:
    status: str
    credentials: dict[str, str] = field(default_factory=dict)
    message: str = ""


@dataclass
class AccountProfile:
    remote_account_id: str
    display_name: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class UploadResult:
    media_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubmissionResult:
    remote_video_id: str
    remote_url: str
    status: str = "submitted"
    payload: dict[str, Any] = field(default_factory=dict)
