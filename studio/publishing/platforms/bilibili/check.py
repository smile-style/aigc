from studio.publishing.base import AccountProfile
from studio.publishing.errors import PublishingAuthError, PublishingValidationError

from .client import BilibiliClient


class BilibiliChecker:
    NAV_URL = "https://api.bilibili.com/x/web-interface/nav"

    def check_account(self, credentials):
        client = BilibiliClient(credentials, timeout=20)
        try:
            data, _ = client.checked_data("GET", self.NAV_URL)
        finally:
            client.close()
        if not data or not data.get("isLogin"):
            raise PublishingAuthError()
        return AccountProfile(
            remote_account_id=str(data.get("mid") or ""),
            display_name=data.get("uname") or "Bilibili 账号",
            payload={"mid": data.get("mid"), "uname": data.get("uname"), "face": data.get("face", "")},
        )

    def check_submission(self, composition, metadata):
        if not composition.video:
            raise PublishingValidationError("成片文件不存在。", code="missing_video")
        title = str(metadata.get("title") or "").strip()
        if not title:
            raise PublishingValidationError("请填写稿件标题。", details={"field": "title"})
        if len(title) > 80:
            raise PublishingValidationError("稿件标题不能超过 80 个字符。", details={"field": "title"})
        try:
            tid = int(metadata.get("tid"))
        except (TypeError, ValueError) as exc:
            raise PublishingValidationError("请选择有效的投稿分区。", details={"field": "tid"}) from exc
        try:
            copyright_value = int(metadata.get("copyright", 1))
        except (TypeError, ValueError) as exc:
            raise PublishingValidationError("稿件类型无效。", details={"field": "copyright"}) from exc
        if copyright_value not in {1, 2}:
            raise PublishingValidationError("稿件类型无效。", details={"field": "copyright"})
        if copyright_value == 2 and not str(metadata.get("source") or "").strip():
            raise PublishingValidationError("转载稿件需要填写原作地址。", details={"field": "source"})
        return {**metadata, "title": title, "tid": tid, "copyright": copyright_value}
