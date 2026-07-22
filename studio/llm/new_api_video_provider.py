import base64
import mimetypes
import os

import httpx

from studio.llm.provider import LLMAPIError, LLMConfigurationError
from studio.llm.video_provider import VideoSubmission, VideoTaskResult


class NewApiVideoProvider:
    CREATE_PATH = "/video/generations"
    TASK_PATH = "/video/generations/{task_id}"
    MAX_REFERENCE_IMAGES = 5
    SEEDANCE_MIN_DURATION = 4

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
        if len(image_paths) > self.MAX_REFERENCE_IMAGES:
            raise ValueError(f"视频网关最多支持 {self.MAX_REFERENCE_IMAGES} 张角色参考图。")
        settings = self._parameters(parameters)
        metadata = {
            key: value
            for key, value in settings.items()
            if key
            in {
                "resolution",
                "ratio",
                "seed",
                "camera_fixed",
                "watermark",
                "generate_audio",
                "draft",
                "service_tier",
                "priority",
            }
        }
        if "resolution" in metadata:
            metadata["resolution"] = str(metadata["resolution"]).lower()
        if negative_prompt.strip():
            metadata["negative_prompt"] = negative_prompt.strip()
        if image_paths:
            reference_role = str(settings.get("reference_image_role") or "reference_image")
            metadata["content"] = [
                {
                    "type": "image_url",
                    "image_url": {"url": self._file_data_uri(path)},
                    "role": reference_role,
                }
                for path in image_paths
            ]
        payload = {
            "model": self.model_config.model_id,
            "prompt": prompt,
            "seconds": str(settings["duration"]),
            "metadata": metadata,
        }
        response = self.client.post(
            f"{self._origin()}{self.CREATE_PATH}",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout,
        )
        data = self._response_json(response)
        task_id = str(data.get("task_id") or data.get("id") or "")
        if not task_id:
            raise LLMAPIError("视频网关没有返回任务 ID。")
        return VideoSubmission(
            task_id=task_id,
            request_id=str(data.get("request_id") or ""),
        )

    def get_task_result(self, task_id):
        response = self.client.get(
            f"{self._origin()}{self.TASK_PATH.format(task_id=task_id)}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        payload = self._response_json(response)
        task = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        upstream = task.get("data") if isinstance(task.get("data"), dict) else {}
        status = str(task.get("status") or upstream.get("status") or "UNKNOWN").upper()
        status_map = {
            "QUEUED": "pending",
            "PENDING": "pending",
            "IN_PROGRESS": "running",
            "PROCESSING": "running",
            "RUNNING": "running",
            "SUCCESS": "succeeded",
            "SUCCEEDED": "succeeded",
            "COMPLETED": "succeeded",
            "FAILURE": "failed",
            "FAILED": "failed",
            "CANCELLED": "failed",
            "EXPIRED": "failed",
        }
        normalized_status = status_map.get(status, "running")
        content = upstream.get("content") if isinstance(upstream.get("content"), dict) else {}
        video_url = str(
            task.get("result_url")
            or task.get("url")
            or content.get("video_url")
            or ""
        )
        error = upstream.get("error") if isinstance(upstream.get("error"), dict) else {}
        message = str(
            task.get("fail_reason")
            or task.get("message")
            or error.get("message")
            or ""
        )
        return VideoTaskResult(
            status=normalized_status,
            video_url=video_url,
            message=message,
            payload=payload,
        )

    def download(self, url):
        response = self.client.get(url, timeout=self.timeout, follow_redirects=True)
        response.raise_for_status()
        return response.content

    def _origin(self):
        return self.provider_config.base_url.rstrip("/")

    def _parameters(self, overrides):
        parameters = dict(self.model_config.default_parameters or {})
        parameters.update(overrides or {})
        minimum_duration = (
            self.SEEDANCE_MIN_DURATION
            if self.model_config.model_id.lower().startswith("doubao-seedance")
            else 2
        )
        parameters["duration"] = max(
            minimum_duration, min(15, int(parameters.get("duration", 5)))
        )
        return parameters

    def _headers(self):
        if not self.api_key:
            raise LLMConfigurationError("视频网关 Token 未配置。")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

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
                error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                message = payload.get("message") or error.get("message")
            except (ValueError, TypeError, AttributeError):
                message = exc.response.text[:500]
            raise LLMAPIError(f"视频网关请求失败：{message or exc}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMAPIError(f"视频网关请求失败：{exc}") from exc
        code = str(data.get("code") or "").lower()
        if code and code not in {"success", "ok", "200"}:
            raise LLMAPIError(f"视频网关请求失败：{data.get('message') or code}")
        return data
