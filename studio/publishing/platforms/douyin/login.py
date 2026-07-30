import os
import secrets
import time
from urllib.parse import urlencode

from studio.publishing.base import LoginStart
from studio.publishing.errors import PublishingAuthError, PublishingValidationError

from .client import DouyinClient


class DouyinLogin:
    AUTHORIZE_URL = "https://open.douyin.com/platform/oauth/connect/"

    @staticmethod
    def _config():
        client_key = os.environ.get("DOUYIN_CLIENT_KEY", "").strip()
        client_secret = os.environ.get("DOUYIN_CLIENT_SECRET", "").strip()
        redirect_uri = os.environ.get("DOUYIN_REDIRECT_URI", "").strip()
        if not client_key or not client_secret or not redirect_uri:
            raise PublishingValidationError(
                "请先配置 DOUYIN_CLIENT_KEY、DOUYIN_CLIENT_SECRET 和 DOUYIN_REDIRECT_URI。",
                code="douyin_not_configured",
            )
        return client_key, client_secret, redirect_uri

    def start(self):
        client_key, _, redirect_uri = self._config()
        state = secrets.token_urlsafe(32)
        scopes = os.environ.get(
            "DOUYIN_SCOPES",
            "user_info,video.create,video.data",
        )
        query = urlencode(
            {
                "client_key": client_key,
                "response_type": "code",
                "scope": scopes,
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
        return LoginStart(
            provider_key=state,
            login_url=f"{self.AUTHORIZE_URL}?{query}",
            expires_in=600,
            mode="oauth",
        )

    def exchange_code(self, code):
        if not code:
            raise PublishingValidationError("抖音授权未返回 code，请重新连接。", code="oauth_code_missing")
        client_key, client_secret, _ = self._config()
        client = DouyinClient(timeout=20)
        try:
            data, _ = client.checked_data(
                "POST",
                client.api_url("/oauth/access_token/"),
                data={
                    "client_key": client_key,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
        finally:
            client.close()
        return self._credentials(data)

    def ensure_credentials(self, credentials):
        try:
            expires_at = int(float(credentials.get("expires_at") or 0))
        except (TypeError, ValueError):
            expires_at = 0
        if expires_at > int(time.time()) + 300:
            return credentials
        refresh_token = credentials.get("refresh_token")
        if not refresh_token:
            raise PublishingAuthError("抖音授权已过期且无法刷新，请重新连接。")
        client_key, _, _ = self._config()
        client = DouyinClient(timeout=20)
        try:
            data, _ = client.checked_data(
                "POST",
                client.api_url("/oauth/refresh_token/"),
                data={
                    "client_key": client_key,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
        finally:
            client.close()
        refreshed = self._credentials(data)
        if not refreshed.get("refresh_token"):
            refreshed["refresh_token"] = refresh_token
        return refreshed

    @staticmethod
    def _credentials(data):
        access_token = str(data.get("access_token") or "")
        open_id = str(data.get("open_id") or "")
        if not access_token or not open_id:
            raise PublishingAuthError("抖音开放平台未返回完整的授权凭据。")
        now = int(time.time())
        return {
            "access_token": access_token,
            "refresh_token": str(data.get("refresh_token") or ""),
            "open_id": open_id,
            "scope": str(data.get("scope") or ""),
            "expires_at": str(now + int(data.get("expires_in") or 0)),
            "refresh_expires_at": str(now + int(data.get("refresh_expires_in") or 0)),
        }
