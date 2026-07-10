import base64
import json
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .provider import LLMAPIError, LLMConfigurationError


@dataclass(frozen=True)
class ImageResult:
    content: bytes
    extension: str
    source_url: str
    model: str


@dataclass(frozen=True)
class ImageConfig:
    base_url: str
    api_key: str
    model: str
    endpoint: str
    size: str

    @classmethod
    def from_env(cls, environ=None):
        environ = environ or os.environ
        base_url = (environ.get("IMAGE_BASE_URL") or environ.get("LLM_BASE_URL", "")).strip()
        api_key = (environ.get("IMAGE_API_KEY") or environ.get("LLM_API_KEY", "")).strip()
        model = environ.get("IMAGE_MODEL", "gpt-image-2").strip()
        endpoint = environ.get("IMAGE_API_PATH", "/chat/completions").strip()
        if model == "gpt-image-2" and endpoint.strip("/") == "images/generations":
            endpoint = "/chat/completions"
        size = environ.get("IMAGE_SIZE", "1024x1536").strip()
        missing = [
            name
            for name, value in {
                "IMAGE_BASE_URL/LLM_BASE_URL": base_url,
                "IMAGE_API_KEY/LLM_API_KEY": api_key,
                "IMAGE_MODEL": model,
            }.items()
            if not value
        ]
        if missing:
            raise LLMConfigurationError(f"Missing image configuration: {', '.join(missing)}")
        return cls(base_url.rstrip("/"), api_key, model, "/" + endpoint.strip("/"), size)


class ImageProvider:
    DEFAULT_TIMEOUT_SECONDS = 600
    DEFAULT_MAX_RETRIES = 3

    def __init__(self, config, client=None):
        self.config = config
        trust_env = os.environ.get(
            "IMAGE_TRUST_ENV",
            os.environ.get("LLM_TRUST_ENV", "false"),
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.client = client or httpx.Client(trust_env=trust_env)
        self.timeout = float(os.environ.get("IMAGE_TIMEOUT_SECONDS", self.DEFAULT_TIMEOUT_SECONDS))
        self.max_retries = int(os.environ.get("IMAGE_MAX_RETRIES", self.DEFAULT_MAX_RETRIES))

    @classmethod
    def from_env(cls, environ=None):
        return cls(ImageConfig.from_env(environ))

    def generate_image(self, prompt):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Image prompt must be a non-empty string")
        url = f"{self.config.base_url}{self.config.endpoint}"
        if self.config.endpoint.endswith("/chat/completions"):
            payload = {
                "model": self.config.model,
                "messages": [
                    {
                        "role": "user",
                        "content": f"{prompt.strip()}\n输出尺寸：{self.config.size}",
                    }
                ],
                "stream": False,
            }
        else:
            payload = {
                "model": self.config.model,
                "prompt": prompt,
                "n": 1,
                "size": self.config.size,
            }
        try:
            response = self._post_with_retry(
                url,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            content, source_url = self._extract_image(response.json())
            return ImageResult(
                content=content,
                extension=self._extension(source_url),
                source_url=source_url,
                model=self.config.model,
            )
        except httpx.TimeoutException as exc:
            raise LLMAPIError("Image generation timed out") from exc
        except httpx.HTTPError as exc:
            raise LLMAPIError(f"Image generation request failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMAPIError("Image response did not include image data") from exc

    def _post_with_retry(self, url, **kwargs):
        for attempt in range(self.max_retries + 1):
            response = self.client.post(url, **kwargs)
            status_code = getattr(response, "status_code", 200)
            if status_code not in {502, 503, 504} or attempt >= self.max_retries:
                return response
            time.sleep(2 ** attempt)
        raise RuntimeError("Unreachable retry state")

    def _extract_image(self, payload):
        item = (payload.get("data") or [None])[0]
        if isinstance(item, dict):
            encoded = item.get("b64_json")
            if encoded:
                return base64.b64decode(encoded), ""
            source_url = item.get("url", "")
            if source_url.startswith("data:"):
                header, encoded = source_url.split(",", 1)
                return base64.b64decode(encoded), ""
            if source_url:
                response = self.client.get(source_url, timeout=self.timeout)
                response.raise_for_status()
                return response.content, source_url

        choices = payload.get("choices") or []
        if choices and isinstance(choices[0], dict):
            reference = self._find_image_reference(choices[0].get("message", {}))
            if reference:
                return self._load_reference(reference)

        raise ValueError("Missing image URL")

    def _find_image_reference(self, value):
        if isinstance(value, dict):
            encoded = value.get("b64_json")
            if isinstance(encoded, str) and encoded:
                return ("base64", encoded)
            for key in ("url", "image_url", "images", "content"):
                if key in value:
                    reference = self._find_image_reference(value[key])
                    if reference:
                        return reference
            return None
        if isinstance(value, list):
            for item in value:
                reference = self._find_image_reference(item)
                if reference:
                    return reference
            return None
        if not isinstance(value, str):
            return None

        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                reference = self._find_image_reference(json.loads(text))
                if reference:
                    return reference
            except json.JSONDecodeError:
                pass
        data_match = re.search(
            r"data:image/(?:png|jpe?g|webp);base64,([A-Za-z0-9+/=\r\n]+)",
            text,
            flags=re.IGNORECASE,
        )
        if data_match:
            return ("base64", data_match.group(1))
        markdown_match = re.search(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", text)
        if markdown_match:
            return ("url", markdown_match.group(1))
        url_match = re.search(r"https?://[^\s<>)]+", text)
        if url_match:
            return ("url", url_match.group(0))
        return None

    def _load_reference(self, reference):
        kind, value = reference
        if kind == "base64":
            return base64.b64decode(value), ""
        response = self.client.get(value, timeout=self.timeout)
        response.raise_for_status()
        return response.content, value

    @staticmethod
    def _extension(source_url):
        suffix = os.path.splitext(urlparse(source_url).path)[1].lower()
        return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
