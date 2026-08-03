import base64
import json
import mimetypes
import os

import httpx

from studio.llm.provider import LLMAPIError, LLMConfigurationError
from studio.llm.video_provider import VideoSubmission, VideoTaskResult


class MiniMaxH3VideoProvider:
    CREATE_PATH = "/v2/video_generation"
    TASK_PATH = "/v2/query/video_generation/{task_id}"
    MAX_REFERENCE_IMAGES = 9
    MAX_REQUEST_BYTES = 64 * 1024 * 1024
    MAX_IMAGE_BYTES = 30 * 1024 * 1024
    ALLOWED_RATIOS = {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16", "adaptive"}

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
        if any(os.path.getsize(path) > self.MAX_IMAGE_BYTES for path in image_paths):
            raise ValueError("MiniMax H3 单张参考图不能超过 30 MB。")
        if len(image_paths) > self.MAX_REFERENCE_IMAGES:
            raise ValueError(f"MiniMax H3 最多支持 {self.MAX_REFERENCE_IMAGES} 张参考图。")

        settings = self._parameters(parameters, has_references=bool(image_paths))
        prompt = str(prompt).strip()
        if not prompt:
            raise ValueError("MiniMax H3 视频提示词不能为空。")
        if negative_prompt.strip():
            prompt = f"{prompt}\n\n避免：{negative_prompt.strip()}"

        content = [{"type": "text", "text": prompt}]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": self._file_data_uri(path)},
                "role": "reference_image",
            }
            for path in image_paths
        )
        payload = {
            "model": self.model_config.model_id,
            "content": content,
            "resolution": settings["resolution"],
            "duration": settings["duration"],
            "ratio": settings["ratio"],
            "aigc_watermark": settings["aigc_watermark"],
        }
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > self.MAX_REQUEST_BYTES:
            raise ValueError("MiniMax H3 请求体超过 64 MB，请减少或压缩参考图。")

        response = self.client.post(
            f"{self._origin()}{self.CREATE_PATH}",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout,
        )
        data = self._response_json(response)
        task_id = str(data.get("task_id") or "")
        if not task_id:
            raise LLMAPIError("MiniMax H3 没有返回视频任务 ID。")
        return VideoSubmission(task_id=task_id, request_id=str(data.get("request_id") or ""))

    def get_task_result(self, task_id):
        response = self.client.get(
            f"{self._origin()}{self.TASK_PATH.format(task_id=task_id)}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        payload = self._response_json(response)
        task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
        if not task:
            raise LLMAPIError("MiniMax H3 查询响应中缺少任务信息。")
        status = str(task.get("status") or "running").lower()
        status_map = {
            "queued": "pending",
            "running": "running",
            "succeeded": "succeeded",
            "failed": "failed",
            "cancelled": "failed",
        }
        content = task.get("content") if isinstance(task.get("content"), dict) else {}
        error = task.get("error") if isinstance(task.get("error"), dict) else {}
        return VideoTaskResult(
            status=status_map.get(status, "running"),
            video_url=str(content.get("url") or ""),
            message=str(error.get("message") or task.get("message") or ""),
            payload=payload,
        )

    def download(self, url):
        response = self.client.get(url, timeout=self.timeout, follow_redirects=True)
        response.raise_for_status()
        return response.content

    def _origin(self):
        value = self.provider_config.base_url.rstrip("/")
        return value[:-3] if value.endswith("/v2") else value

    def _parameters(self, overrides, has_references=False):
        parameters = dict(self.model_config.default_parameters or {})
        parameters.update(overrides or {})
        resolution = str(parameters.get("resolution") or "768P").upper()
        if resolution == "720P":
            resolution = "768P"
        if resolution not in {"768P", "2K"}:
            raise ValueError("MiniMax H3 分辨率只支持 768P 或 2K。")
        ratio = str(parameters.get("ratio") or "9:16")
        if ratio not in self.ALLOWED_RATIOS:
            raise ValueError(f"MiniMax H3 不支持画面比例 {ratio}。")
        if ratio == "adaptive" and not has_references:
            ratio = "9:16"
        return {
            "resolution": resolution,
            "duration": max(4, min(15, int(parameters.get("duration", 5)))),
            "ratio": ratio,
            "aigc_watermark": bool(
                parameters.get("aigc_watermark", parameters.get("watermark", False))
            ),
        }

    def _headers(self):
        if not self.api_key:
            raise LLMConfigurationError("MiniMax API Token 未配置。")
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
            return response.json()
        except httpx.HTTPStatusError as exc:
            try:
                payload = exc.response.json()
                error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                message = payload.get("message") or error.get("message")
            except (ValueError, TypeError, AttributeError):
                message = exc.response.text[:500]
            raise LLMAPIError(f"MiniMax H3 视频请求失败：{message or exc}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMAPIError(f"MiniMax H3 视频请求失败：{exc}") from exc
