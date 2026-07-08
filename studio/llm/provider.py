import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx


class LLMConfigurationError(RuntimeError):
    pass


class LLMAPIError(RuntimeError):
    pass


class LLMJSONParseError(RuntimeError):
    def __init__(self, raw_text):
        super().__init__("Model returned invalid JSON")
        self.raw_text = raw_text


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str

    @classmethod
    def from_env(cls, environ=None):
        if environ is None:
            environ = os.environ
        base_url = environ.get("LLM_BASE_URL", "").strip()
        api_key = environ.get("LLM_API_KEY", "").strip()
        model = environ.get("LLM_MODEL", "").strip()
        missing = [
            name
            for name, value in {
                "LLM_BASE_URL": base_url,
                "LLM_API_KEY": api_key,
                "LLM_MODEL": model,
            }.items()
            if not value
        ]
        if missing:
            raise LLMConfigurationError(f"Missing model configuration: {', '.join(missing)}")
        return cls(base_url=base_url.rstrip("/"), api_key=api_key, model=model)


class LLMProvider:
    DEFAULT_TIMEOUT_SECONDS = 300
    DEFAULT_LOG_PATH = "logs/llm.log"

    def __init__(self, config, client=None, timeout=None):
        self.config = config
        self.client = client or httpx.Client(trust_env=False)
        self.timeout = timeout or self._timeout_from_env()

    @classmethod
    def from_env(cls, environ=None):
        return cls(LLMConfig.from_env(environ))

    @classmethod
    def _timeout_from_env(cls):
        raw_timeout = os.environ.get("LLM_TIMEOUT_SECONDS", "").strip()
        if not raw_timeout:
            return cls.DEFAULT_TIMEOUT_SECONDS
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise LLMConfigurationError("LLM_TIMEOUT_SECONDS must be a number") from exc
        if timeout <= 0:
            raise LLMConfigurationError("LLM_TIMEOUT_SECONDS must be greater than 0")
        return timeout

    def close(self):
        close = getattr(self.client, "close", None)
        if close is not None:
            close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def generate_text(self, messages, temperature=None):
        url = f"{self.config.base_url}/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature

        started_at = time.perf_counter()
        self._log_event(
            "request.start",
            url=url,
            model=self.config.model,
            timeout_seconds=self.timeout,
            temperature=temperature,
            message_count=len(messages),
            stream=False,
        )

        try:
            response = self.client.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
            self._log_event(
                "request.end",
                url=url,
                model=self.config.model,
                elapsed_ms=elapsed_ms,
                status_code=response.status_code,
                content_type=response.headers.get("content-type", ""),
                response_bytes=len(response.content),
                request_id=response.headers.get("x-request-id", ""),
            )
            response.raise_for_status()
            content = self._extract_message_content(response)
            self._log_event(
                "response.parsed",
                url=url,
                model=self.config.model,
                elapsed_ms=elapsed_ms,
                content_chars=len(content),
            )
            return content
        except httpx.TimeoutException as exc:
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
            self._log_event(
                "request.timeout",
                url=url,
                model=self.config.model,
                elapsed_ms=elapsed_ms,
                timeout_seconds=self.timeout,
                error=str(exc),
            )
            raise LLMAPIError(f"Model request timed out after {self.timeout:g} seconds") from exc
        except httpx.HTTPStatusError as exc:
            response = exc.response
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
            self._log_event(
                "request.http_error",
                url=url,
                model=self.config.model,
                elapsed_ms=elapsed_ms,
                status_code=response.status_code,
                content_type=response.headers.get("content-type", ""),
                response_bytes=len(response.content),
                request_id=response.headers.get("x-request-id", ""),
                body_preview=response.text[:1000],
                error=str(exc),
            )
            raise LLMAPIError(str(exc)) from exc
        except httpx.HTTPError as exc:
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
            self._log_event(
                "request.transport_error",
                url=url,
                model=self.config.model,
                elapsed_ms=elapsed_ms,
                error=str(exc),
            )
            raise LLMAPIError(str(exc)) from exc
        except (json.JSONDecodeError, ValueError, KeyError, IndexError, TypeError) as exc:
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
            self._log_event(
                "response.parse_error",
                url=url,
                model=self.config.model,
                elapsed_ms=elapsed_ms,
                error=str(exc),
            )
            raise LLMAPIError("Model response did not include message content") from exc

    @staticmethod
    def _extract_message_content(response):
        content_type = response.headers.get("content-type", "").lower()
        if "text/event-stream" in content_type:
            chunks = []
            for line in response.text.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data_text = line.removeprefix("data:").strip()
                if not data_text or data_text == "[DONE]":
                    continue
                data = json.loads(data_text)
                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                content = delta.get("content")
                if content:
                    chunks.append(content)
            return "".join(chunks)

        data = response.json()
        return data["choices"][0]["message"]["content"]

    def generate_json(self, messages, temperature=None):
        raw_text = self.generate_text(messages, temperature=temperature)
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise LLMJSONParseError(raw_text) from exc

    @classmethod
    def _log_event(cls, event, **fields):
        logger = cls._logger()
        payload = {"event": event, **fields}
        logger.info(json.dumps(payload, ensure_ascii=False, default=str))

    @classmethod
    def _logger(cls):
        logger = logging.getLogger("studio.llm")
        if not logger.handlers:
            log_path = cls._log_path()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(log_path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
        return logger

    @classmethod
    def _log_path(cls):
        raw_path = os.environ.get("LLM_LOG_PATH", cls.DEFAULT_LOG_PATH).strip() or cls.DEFAULT_LOG_PATH
        path = Path(raw_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        return path