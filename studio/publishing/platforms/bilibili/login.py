from studio.publishing.base import LoginPoll, LoginStart
from studio.publishing.errors import PublishingRetryableError

from .client import BilibiliClient


class BilibiliLogin:
    GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

    def start(self):
        client = BilibiliClient(timeout=20)
        try:
            data, _ = client.checked_data("GET", self.GENERATE_URL)
        finally:
            client.close()
        if not data or not data.get("qrcode_key") or not data.get("url"):
            raise PublishingRetryableError("Bilibili 未返回可用的登录二维码。")
        return LoginStart(provider_key=data["qrcode_key"], login_url=data["url"])

    def poll(self, provider_key):
        client = BilibiliClient(timeout=20)
        try:
            _, payload = client.request_json("GET", self.POLL_URL, params={"qrcode_key": provider_key})
            data = payload.get("data") or {}
            code = data.get("code")
            if payload.get("code") != 0:
                raise PublishingRetryableError(payload.get("message") or "二维码登录查询失败。")
            if code == 0:
                cookies = {item.name: item.value for item in client.client.cookies.jar}
                return LoginPoll("succeeded", credentials=cookies)
            if code == 86090:
                return LoginPoll("scanned", message="已扫码，请在手机上确认登录。")
            if code == 86038:
                return LoginPoll("expired", message="二维码已过期。")
            return LoginPoll("pending", message="等待扫码。")
        finally:
            client.close()

    @staticmethod
    def parse_cookie_header(raw_cookie):
        cookies = {}
        for item in str(raw_cookie or "").split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name and value:
                cookies[name] = value
        return cookies
