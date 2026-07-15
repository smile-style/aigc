import base64
import mimetypes
import os
from dataclasses import dataclass

import httpx

from studio.llm.provider import LLMAPIError, LLMConfigurationError


@dataclass(frozen=True)
class VideoSubmission:
    task_id: str
    request_id: str


@dataclass(frozen=True)
class VideoTaskResult:
    status: str
    video_url: str
    message: str
    payload: dict


class BailianVideoProvider:
    CREATE_PATH = "/api/v1/services/aigc/video-generation/video-synthesis"
    TASK_PATH = "/api/v1/tasks/{task_id}"

    def __init__(self, model_config, api_key, client=None):
        self.model_config = model_config
        self.provider_config = model_config.provider
        self.api_key = api_key
        self.client = client or httpx.Client(trust_env=False)
        self.timeout = self.provider_config.timeout_seconds

    def close(self):
        self.client.close()

    def submit_video(self, prompt, image_paths=None, parameters=None, negative_prompt=""):
        image_paths = list(image_paths or [])
        if len(image_paths) > 5:
            raise ValueError("万相参考生视频最多支持 5 个角色参考素材。")
        input_payload = {"prompt": prompt}
        if image_paths:
            input_payload["media"] = [
                {"type": "reference_image", "url": self._file_data_uri(path)}
                for path in image_paths
            ]
        if negative_prompt.strip():
            input_payload["negative_prompt"] = negative_prompt.strip()
        payload = {
            "model": self.model_config.model_id,
            "input": input_payload,
            "parameters": self._parameters(parameters),
        }
        response = self.client.post(
            f"{self._origin()}{self.CREATE_PATH}",
            headers=self._headers(async_request=True),
            json=payload,
            timeout=self.timeout,
        )
        data = self._response_json(response)
        output = data.get("output") or {}
        task_id = str(output.get("task_id") or "")
        if not task_id:
            raise LLMAPIError("百炼没有返回视频任务 ID。")
        return VideoSubmission(task_id=task_id, request_id=str(data.get("request_id") or ""))

    def submit_reference_video(self, prompt, image_paths, parameters=None, negative_prompt=""):
        return self.submit_video(prompt, image_paths, parameters, negative_prompt)

    def get_task(self, task_id):
        response = self.client.get(
            f"{self._origin()}{self.TASK_PATH.format(task_id=task_id)}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        return self._response_json(response)

    def get_task_result(self, task_id):
        payload = self.get_task(task_id)
        output = payload.get("output") or {}
        status = str(output.get("task_status") or "UNKNOWN").upper()
        status_map = {
            "PENDING": "pending",
            "RUNNING": "running",
            "SUCCEEDED": "succeeded",
            "FAILED": "failed",
            "CANCELED": "failed",
            "CANCELLED": "failed",
        }
        return VideoTaskResult(
            status=status_map.get(status, "running"),
            video_url=str(output.get("video_url") or ""),
            message=str(output.get("message") or payload.get("message") or ""),
            payload=payload,
        )

    def download(self, url):
        response = self.client.get(url, timeout=self.timeout, follow_redirects=True)
        response.raise_for_status()
        return response.content

    def _origin(self):
        value = self.provider_config.base_url.rstrip("/")
        suffix = "/compatible-mode/v1"
        return value[: -len(suffix)] if value.endswith(suffix) else value

    def _parameters(self, overrides):
        parameters = dict(self.model_config.default_parameters or {})
        parameters.update(overrides or {})
        parameters["resolution"] = "720P"
        parameters["duration"] = max(2, min(15, int(parameters.get("duration", 5))))
        return parameters

    def _headers(self, async_request=False):
        if not self.api_key:
            raise LLMConfigurationError("百炼 API Key 未配置。")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if async_request:
            headers["X-DashScope-Async"] = "enable"
        return headers

    @staticmethod
    def _file_data_uri(path):
        path = os.fspath(path)
        mime_type = mimetypes.guess_type(path)[0] or "image/png"
        with open(path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _response_json(response):
        try:
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            try:
                payload = exc.response.json()
                message = payload.get("message") or payload.get("error", {}).get("message")
            except (ValueError, TypeError, AttributeError):
                message = exc.response.text[:500]
            raise LLMAPIError(f"百炼视频请求失败：{message or exc}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMAPIError(f"百炼视频请求失败：{exc}") from exc
        if data.get("code"):
            raise LLMAPIError(f"百炼视频请求失败：{data.get('message') or data['code']}")
        return data
