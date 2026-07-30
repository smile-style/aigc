import httpx

from studio.publishing.errors import (
    PublishingAuthError,
    PublishingRetryableError,
    PublishingValidationError,
)


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)


class AcFunClient:
    def __init__(self, cookies=None, *, timeout=120, client=None):
        self._owns_client = client is None
        self.client = client or httpx.Client(
            cookies=cookies or {},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://member.acfun.cn",
                "Referer": "https://member.acfun.cn/",
            },
            timeout=httpx.Timeout(timeout, connect=15),
            follow_redirects=True,
        )

    def request_json(self, method, url, **kwargs):
        try:
            response = self.client.request(method, url, **kwargs)
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise PublishingRetryableError(
                "连接 AcFun 失败，请稍后重试。", details={"type": type(exc).__name__}
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                raise PublishingAuthError("AcFun 登录已失效，请重新导入 Cookie。") from exc
            if status == 429 or status >= 500:
                raise PublishingRetryableError(f"AcFun 暂时不可用（HTTP {status}）。") from exc
            raise PublishingValidationError(f"AcFun 拒绝了请求（HTTP {status}）。") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise PublishingRetryableError("AcFun 返回了无法解析的响应。") from exc
        if not isinstance(payload, dict):
            raise PublishingRetryableError("AcFun 返回的数据格式无效。")
        return response, payload

    @staticmethod
    def message(payload):
        details = payload.get("errMsg")
        if not isinstance(details, dict):
            details = {}
        return str(
            details.get("error_msg")
            or payload.get("error_msg")
            or payload.get("message")
            or payload.get("msg")
            or "AcFun 请求失败。"
        )

    def close(self):
        if self._owns_client:
            self.client.close()
