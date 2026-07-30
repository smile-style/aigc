import os

import httpx

from studio.publishing.errors import (
    PublishingAuthError,
    PublishingRetryableError,
    PublishingValidationError,
)


AUTH_ERROR_CODES = {10008, 10010, 10011, 2190008}
RATE_LIMIT_CODES = {2100005, 2190004}


class DouyinClient:
    def __init__(self, credentials=None, *, timeout=120, client=None):
        self.credentials = credentials or {}
        self.base_url = os.environ.get("DOUYIN_API_BASE_URL", "https://open.douyin.com").rstrip("/")
        self._owns_client = client is None
        self.client = client or httpx.Client(
            headers={"User-Agent": "AIGC-Studio/1.0"},
            timeout=httpx.Timeout(timeout, connect=10),
            follow_redirects=True,
        )

    def api_url(self, path):
        return f"{self.base_url}/{path.lstrip('/')}"

    def auth_params(self):
        access_token = self.credentials.get("access_token")
        open_id = self.credentials.get("open_id")
        if not access_token or not open_id:
            raise PublishingAuthError("抖音授权凭据不完整，请重新连接。")
        return {"access_token": access_token, "open_id": open_id}

    def request(self, method, url, **kwargs):
        try:
            response = self.client.request(method, url, **kwargs)
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise PublishingRetryableError(
                "连接抖音开放平台失败，请稍后重试。",
                details={"type": type(exc).__name__},
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            payload = self._error_payload(exc.response)
            details = {"http_status": status}
            code, message = self._error(payload)
            if code:
                details["platform_code"] = code
            if message:
                details["platform_message"] = message
            if status in {401, 403}:
                raise PublishingAuthError(message or "抖音授权已失效。", details=details) from exc
            if status == 429 or status >= 500:
                raise PublishingRetryableError(
                    message or f"抖音开放平台暂时不可用（HTTP {status}）。",
                    code="platform_rate_limited" if status == 429 else "temporary_platform_error",
                    details=details,
                ) from exc
            raise PublishingValidationError(
                message or f"抖音开放平台拒绝了请求（HTTP {status}）。",
                code=f"douyin_http_{status}",
                details=details,
            ) from exc
        return response

    def request_json(self, method, url, **kwargs):
        response = self.request(method, url, **kwargs)
        try:
            payload = response.json()
        except ValueError as exc:
            raise PublishingRetryableError("抖音开放平台返回了无法解析的响应。") from exc
        if not isinstance(payload, dict):
            raise PublishingRetryableError("抖音开放平台返回了无效响应。")
        return response, payload

    def checked_data(self, method, url, **kwargs):
        _, payload = self.request_json(method, url, **kwargs)
        code, message = self._error(payload)
        if not code:
            return payload.get("data") or {}, payload
        details = {"platform_code": code, "platform_message": message}
        normalized = message.lower()
        if code in AUTH_ERROR_CODES or "access_token" in normalized or "授权" in message:
            raise PublishingAuthError(message or "抖音授权已失效。", details=details)
        if code in RATE_LIMIT_CODES or "频繁" in message or "限流" in message:
            raise PublishingRetryableError(
                message or "抖音接口调用过于频繁，请稍后重试。",
                code="platform_rate_limited",
                details={**details, "retry_after_seconds": 600},
            )
        raise PublishingValidationError(
            message or f"抖音接口错误 {code}",
            code=f"douyin_{code}",
            details=details,
        )

    @staticmethod
    def _error(payload):
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        code = payload.get("error_code")
        if code in {None, 0, "0"}:
            code = data.get("error_code", 0)
        try:
            code = int(code or 0)
        except (TypeError, ValueError):
            code = -1
        message = str(
            payload.get("description")
            or payload.get("message")
            or data.get("description")
            or data.get("message")
            or ""
        )
        return code, message

    @staticmethod
    def _error_payload(response):
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def close(self):
        if self._owns_client:
            self.client.close()
