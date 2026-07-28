import hashlib
from urllib.parse import unquote

from studio.publishing.base import AccountProfile
from studio.publishing.errors import PublishingAuthError, PublishingValidationError

from .client import AcFunClient


class AcFunChecker:
    CHANNELS_URL = "https://member.acfun.cn/video/api/getMyChannels"

    def check_account(self, credentials):
        client = AcFunClient(credentials, timeout=20)
        try:
            _, payload = client.request_json("GET", self.CHANNELS_URL)
        finally:
            client.close()
        if payload.get("result") != 0:
            raise PublishingAuthError(AcFunClient.message(payload))
        remote_id = self._first(credentials, "ac_userId", "userId", "userid", "acfun_user_id")
        if not remote_id:
            token = self._first(credentials, "acPasstoken", "auth_key", "acfun.midground.api_st")
            if not token:
                raise PublishingAuthError("AcFun Cookie 缺少登录令牌，请重新导入。")
            remote_id = hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
        display_name = unquote(self._first(credentials, "ac_username", "username") or "AcFun 账号")
        return AccountProfile(
            remote_account_id=remote_id,
            display_name=display_name,
            payload={"user_id": remote_id, "display_name": display_name},
        )

    def check_submission(self, composition, metadata):
        if not composition.video:
            raise PublishingValidationError("成片文件不存在。", code="missing_video")
        title = str(metadata.get("title") or "").strip()
        if not title:
            raise PublishingValidationError("请填写稿件标题。", details={"field": "title"})
        if len(title) > 50:
            raise PublishingValidationError("AcFun 稿件标题不能超过 50 个字符。", details={"field": "title"})
        description = str(metadata.get("description") or "").strip()
        if len(description) > 1000:
            raise PublishingValidationError("AcFun 稿件简介不能超过 1000 个字符。", details={"field": "description"})
        tags = list(metadata.get("tags") or [])
        if len(tags) > 6:
            raise PublishingValidationError("AcFun 稿件最多填写 6 个标签。", details={"field": "tags"})
        try:
            channel_id = int(metadata.get("tid"))
        except (TypeError, ValueError) as exc:
            raise PublishingValidationError("请选择有效的 AcFun 分区。", details={"field": "tid"}) from exc
        try:
            copyright_value = int(metadata.get("copyright", 1))
        except (TypeError, ValueError) as exc:
            raise PublishingValidationError("稿件类型无效。", details={"field": "copyright"}) from exc
        if copyright_value not in {1, 2}:
            raise PublishingValidationError("稿件类型无效。", details={"field": "copyright"})
        if copyright_value == 2 and not str(metadata.get("source") or "").strip():
            raise PublishingValidationError("转载稿件需要填写原作地址。", details={"field": "source"})
        if not str(metadata.get("cover") or "").strip():
            raise PublishingValidationError("发布到 AcFun 需要单集封面。", details={"field": "cover"})
        return {
            **metadata,
            "title": title,
            "description": description,
            "tid": channel_id,
            "copyright": copyright_value,
            "tags": tags,
        }

    @staticmethod
    def _first(values, *names):
        for name in names:
            value = str(values.get(name) or "").strip()
            if value:
                return value
        return ""
