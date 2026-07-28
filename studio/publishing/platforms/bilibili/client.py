import httpx

from studio.publishing.errors import (
    PublishingAuthError,
    PublishingRetryableError,
    PublishingValidationError,
)


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

AUTH_ERROR_CODES = {-101, -111, -400, -403}
RATE_LIMIT_CODES = {-412, 601, 21070, 21122}


class BilibiliClient:
    def __init__(self, cookies=None, *, timeout=120, client=None):
        self._owns_client = client is None
        self.client = client or httpx.Client(
            cookies=cookies or {},
            headers={"User-Agent": USER_AGENT, "Referer": "https://member.bilibili.com/"},
            timeout=httpx.Timeout(timeout, connect=10),
            follow_redirects=True,
        )

    def request(self, method, url, **kwargs):
        try:
            response = self.client.request(method, url, **kwargs)
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise PublishingRetryableError("连接 Bilibili 失败，请稍后重试。", details={"type": type(exc).__name__}) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            payload = self._error_payload(exc.response)
            platform_code = payload.get("code")
            platform_message = payload.get("message") or payload.get("msg") or payload.get("info")
            details = {"http_status": status}
            if platform_code is not None:
                details["platform_code"] = platform_code
            if platform_message:
                details["platform_message"] = str(platform_message)
            if platform_code in RATE_LIMIT_CODES:
                details["retry_after_seconds"] = 600
                raise PublishingRetryableError(
                    str(platform_message or "Bilibili upload rate limit reached. Please retry later."),
                    code="platform_rate_limited",
                    details=details,
                ) from exc
            if platform_code in AUTH_ERROR_CODES:
                raise PublishingAuthError(str(platform_message or "Bilibili login has expired."), details=details) from exc
            if platform_message and status not in {401, 403, 429} and status < 500:
                raise PublishingValidationError(str(platform_message), details=details) from exc
            if status in {401, 403}:
                raise PublishingAuthError(details={"http_status": status}) from exc
            if status == 429 or status >= 500:
                raise PublishingRetryableError(f"Bilibili 暂时不可用（HTTP {status}）。") from exc
            raise PublishingValidationError(f"Bilibili 拒绝了请求（HTTP {status}）。") from exc
        return response

    def request_json(self, method, url, **kwargs):
        response = self.request(method, url, **kwargs)
        try:
            payload = response.json()
        except ValueError as exc:
            raise PublishingRetryableError("Bilibili 返回了无法解析的响应。") from exc
        return response, payload

    def checked_data(self, method, url, **kwargs):
        _, payload = self.request_json(method, url, **kwargs)
        code = payload.get("code", 0)
        if code == 0:
            return payload.get("data"), payload
        message = payload.get("message") or payload.get("msg") or f"错误码 {code}"
        if code in AUTH_ERROR_CODES:
            raise PublishingAuthError(message, details={"platform_code": code, "platform_message": message})
        if code in RATE_LIMIT_CODES:
            raise PublishingRetryableError(
                message,
                code="platform_rate_limited",
                details={
                    "platform_code": code,
                    "platform_message": message,
                    "retry_after_seconds": 600,
                },
            )
        raise PublishingValidationError(message, code=f"bilibili_{code}", details={"platform_code": code})

    @staticmethod
    def _error_payload(response):
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def ensure_device_cookies(self):
        if self.client.cookies.get("buvid3"):
            return
        try:
            _, payload = self.request_json(
                "GET", "https://api.bilibili.com/x/frontend/finger/spi"
            )
        except (PublishingRetryableError, PublishingValidationError):
            return
        data = payload.get("data") or {}
        if data.get("b_3"):
            self.client.cookies.set("buvid3", data["b_3"], domain=".bilibili.com")
        if data.get("b_4"):
            self.client.cookies.set("buvid4", data["b_4"], domain=".bilibili.com")

    def close(self):
        if self._owns_client:
            self.client.close()
