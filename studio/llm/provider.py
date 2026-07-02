import json
import os
from dataclasses import dataclass

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
    def __init__(self, config, client=None, timeout=60):
        self.config = config
        self.client = client or httpx.Client()
        self.timeout = timeout

    @classmethod
    def from_env(cls, environ=None):
        return cls(LLMConfig.from_env(environ))

    def close(self):
        close = getattr(self.client, "close", None)
        if close is not None:
            close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def generate_text(self, messages, temperature=None):
        payload = {
            "model": self.config.model,
            "messages": messages,
        }
        if temperature is not None:
            payload["temperature"] = temperature

        try:
            response = self.client.post(
                f"{self.config.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except httpx.HTTPError as exc:
            raise LLMAPIError(str(exc)) from exc
        except (json.JSONDecodeError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMAPIError("Model response did not include message content") from exc

    def generate_json(self, messages, temperature=None):
        raw_text = self.generate_text(messages, temperature=temperature)
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise LLMJSONParseError(raw_text) from exc
