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


class BilibiliClient:
    def __init__(self, cookies=None, *, timeout=120, client=None):
        self._owns_client = client is None
        self.client = client or httpx.Client(
            cookies=cookies or {},
            headers={"User-Agent": USER_AGENT, "Referer": "https://member.bilibili.com/"},
            timeout=httpx.Timeout(timeout, connect=10),
            follow_redirects=True,
        )

    def request_json(self, method, url, **kwargs):
        try:
            response = self.client.request(method, url, **kwargs)
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise PublishingRetryableError("连接 Bilibili 失败，请稍后重试。", details={"type": type(exc).__name__}) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                raise PublishingAuthError(details={"http_status": status}) from exc
            if status == 429 or status >= 500:
                raise PublishingRetryableError(f"Bilibili 暂时不可用（HTTP {status}）。") from exc
            raise PublishingValidationError(f"Bilibili 拒绝了请求（HTTP {status}）。") from exc
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
        if code in {-101, -111, -400, -403}:
            raise PublishingAuthError(message, details={"platform_code": code})
        if code in {-412, 21070, 21122}:
            raise PublishingRetryableError(message, code="platform_rate_limited", details={"platform_code": code})
        raise PublishingValidationError(message, code=f"bilibili_{code}", details={"platform_code": code})

    def close(self):
        if self._owns_client:
            self.client.close()
