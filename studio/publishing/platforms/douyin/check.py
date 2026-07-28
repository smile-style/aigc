from pathlib import PurePosixPath

from studio.publishing.base import AccountProfile
from studio.publishing.errors import PublishingAuthError, PublishingValidationError

from .client import DouyinClient


class DouyinChecker:
    TITLE_LIMIT = 55
    TAG_LIMIT = 5

    def check_account(self, credentials):
        client = DouyinClient(credentials, timeout=20)
        try:
            data, _ = client.checked_data(
                "GET",
                client.api_url("/oauth/userinfo/"),
                params=client.auth_params(),
            )
        finally:
            client.close()
        open_id = str(data.get("open_id") or credentials.get("open_id") or "")
        nickname = str(data.get("nickname") or "抖音账号")
        if not open_id:
            raise PublishingAuthError("抖音开放平台未返回账号标识，请重新授权。")
        return AccountProfile(
            remote_account_id=open_id,
            display_name=nickname,
            payload={
                "open_id": open_id,
                "nickname": nickname,
                "avatar": data.get("avatar") or "",
                "union_id": data.get("union_id") or "",
            },
        )

    def check_submission(self, composition, metadata):
        if not composition.video:
            raise PublishingValidationError("成片文件不存在。", code="missing_video")
        suffix = PurePosixPath(composition.video.name).suffix.lower()
        if suffix not in {".mp4", ".mov"}:
            raise PublishingValidationError(
                "抖音官方接口仅接收 MP4 或 MOV 成片。",
                code="unsupported_video_format",
                details={"field": "video"},
            )
        title = str(metadata.get("title") or "").strip()
        if not title:
            raise PublishingValidationError("请填写抖音作品文案。", details={"field": "title"})
        if len(title) > self.TITLE_LIMIT:
            raise PublishingValidationError(
                f"抖音作品文案不能超过 {self.TITLE_LIMIT} 个字符。",
                details={"field": "title"},
            )
        tags = list(metadata.get("tags") or [])[: self.TAG_LIMIT]
        text = title
        for tag in tags:
            topic = f" #{str(tag).lstrip('#').strip()}"
            if topic.strip() != "#" and len(text) + len(topic) <= self.TITLE_LIMIT:
                text += topic
        return {
            **metadata,
            "title": title,
            "description": str(metadata.get("description") or "").strip(),
            "tags": tags,
            "tid": None,
            "copyright": 1,
            "source": "",
            "platform_text": text,
        }
