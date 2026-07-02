# AI Comic Drama Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local single-user Django web tool that generates AI comic drama outlines, expands a selected outline into a 60-episode script plan plus episode 1 script, and generates storyboard prompts for episode 1.

**Architecture:** Use a Django single app named `studio` with server-rendered templates, light JavaScript, a service layer for generation logic, an OpenAI-compatible provider adapter, and a JSON workspace repository. Views orchestrate requests and rendering; services own prompts and response validation; repositories own file persistence.

**Tech Stack:** Python 3, Django, pytest, pytest-django, httpx, python-dotenv, local JSON files.

---

## Scope Check

The approved spec covers one coherent MVP workflow, not independent products. The implementation can be done as one plan because every subsystem supports the same three-step creative pipeline.

## File Structure

- `.gitignore`: ignore local environment files, Python caches, and generated workspace JSON.
- `.env.example`: document model configuration keys.
- `requirements.txt`: runtime and test dependencies.
- `pytest.ini`: pytest-django configuration.
- `manage.py`: Django command entrypoint.
- `aigc_site/__init__.py`: project package marker.
- `aigc_site/settings.py`: Django settings, dotenv loading, workspace directory.
- `aigc_site/urls.py`: root URL includes `studio.urls`.
- `aigc_site/asgi.py`: ASGI entrypoint.
- `aigc_site/wsgi.py`: WSGI entrypoint.
- `studio/__init__.py`: app package marker.
- `studio/apps.py`: Django app config.
- `studio/constants.py`: genres and fixed V1 generation constraints.
- `studio/llm/__init__.py`: LLM package marker.
- `studio/llm/provider.py`: OpenAI-compatible provider and provider errors.
- `studio/repositories/__init__.py`: repository package marker.
- `studio/repositories/workspace.py`: local JSON workspace repository.
- `studio/services/__init__.py`: service package marker.
- `studio/services/outline.py`: outline generation prompt and validation.
- `studio/services/script.py`: script plan generation prompt and validation.
- `studio/services/storyboard.py`: storyboard prompt generation prompt and validation.
- `studio/urls.py`: named routes for the three-step workflow.
- `studio/views.py`: Django views for pages and POST actions.
- `studio/templates/studio/base.html`: shared page shell.
- `studio/templates/studio/outline.html`: outline generation and selection page.
- `studio/templates/studio/script.html`: script generation page.
- `studio/templates/studio/storyboard.html`: storyboard prompt page.
- `studio/static/studio/app.css`: visual styling.
- `studio/static/studio/app.js`: loading state and duplicate-submit guard.
- `workspace/.gitkeep`: keeps the local workspace directory in git while generated JSON stays ignored.
- `tests/test_project_smoke.py`: baseline Django page test.
- `tests/test_workspace_repository.py`: JSON repository tests.
- `tests/test_llm_provider.py`: provider tests with fake HTTP client.
- `tests/test_outline_service.py`: outline service tests.
- `tests/test_script_service.py`: script service tests.
- `tests/test_storyboard_service.py`: storyboard service tests.
- `tests/test_views.py`: view workflow tests with mocked generation services.
- `README.md`: local setup, model configuration, and manual smoke test.

---

### Task 1: Django Project Scaffold

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `requirements.txt`
- Create: `pytest.ini`
- Create: `manage.py`
- Create: `aigc_site/__init__.py`
- Create: `aigc_site/settings.py`
- Create: `aigc_site/urls.py`
- Create: `aigc_site/asgi.py`
- Create: `aigc_site/wsgi.py`
- Create: `studio/__init__.py`
- Create: `studio/apps.py`
- Create: `studio/constants.py`
- Create: `studio/urls.py`
- Create: `studio/views.py`
- Create: `studio/templates/studio/base.html`
- Create: `studio/templates/studio/outline.html`
- Create: `workspace/.gitkeep`
- Test: `tests/test_project_smoke.py`

- [ ] **Step 1: Write the failing smoke test**

Create `tests/test_project_smoke.py`:

```python
from django.urls import reverse


def test_outline_page_renders(client):
    response = client.get(reverse("studio:outline"))

    assert response.status_code == 200
    assert "AI漫剧生成工具" in response.content.decode("utf-8")
    assert "逆袭爽文" in response.content.decode("utf-8")
```

- [ ] **Step 2: Run the smoke test and verify it fails**

Run:

```bash
pytest tests/test_project_smoke.py -q
```

Expected: FAIL because `pytest.ini`, Django settings, or URL configuration does not exist yet.

- [ ] **Step 3: Add dependency and test configuration files**

Create `.gitignore`:

```gitignore
.env
.venv/
venv/
__pycache__/
*.py[cod]
.pytest_cache/
db.sqlite3
workspace/*.json
```

Create `.env.example`:

```dotenv
LLM_BASE_URL=https://api.example.com/v1
LLM_API_KEY=replace-with-your-key
LLM_MODEL=replace-with-model-name
```

Create `requirements.txt`:

```text
Django>=5.0,<6.0
httpx>=0.27,<1.0
python-dotenv>=1.0,<2.0
pytest>=8.0,<9.0
pytest-django>=4.8,<5.0
```

Create `pytest.ini`:

```ini
[pytest]
DJANGO_SETTINGS_MODULE = aigc_site.settings
python_files = tests.py test_*.py *_tests.py
```

- [ ] **Step 4: Add the minimal Django project and app scaffold**

Create `manage.py`:

```python
#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aigc_site.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
```

Create `aigc_site/__init__.py` as an empty file.

Create `aigc_site/settings.py`:

```python
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = "local-development-key"
DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "studio",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "aigc_site.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    }
]

WSGI_APPLICATION = "aigc_site.wsgi.application"
ASGI_APPLICATION = "aigc_site.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
WORKSPACE_DIR = BASE_DIR / "workspace"
```

Create `aigc_site/urls.py`:

```python
from django.urls import include, path


urlpatterns = [
    path("", include("studio.urls")),
]
```

Create `aigc_site/asgi.py`:

```python
import os

from django.core.asgi import get_asgi_application


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aigc_site.settings")
application = get_asgi_application()
```

Create `aigc_site/wsgi.py`:

```python
import os

from django.core.wsgi import get_wsgi_application


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aigc_site.settings")
application = get_wsgi_application()
```

Create `studio/__init__.py` as an empty file.

Create `studio/apps.py`:

```python
from django.apps import AppConfig


class StudioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "studio"
```

Create `studio/constants.py`:

```python
GENRES = [
    "逆袭爽文",
    "都市修真",
    "霸总甜宠",
    "重生复仇",
    "悬疑惊悚",
    "玄幻修仙",
]

EPISODE_COUNT = 60
EPISODE_DURATION_MINUTES = 2
OUTLINE_CANDIDATE_COUNT = 6
MIN_STORYBOARD_SHOTS = 12
MAX_STORYBOARD_SHOTS = 20
```

Create `studio/urls.py`:

```python
from django.urls import path

from . import views


app_name = "studio"

urlpatterns = [
    path("", views.outline_page, name="outline"),
]
```

Create `studio/views.py`:

```python
from django.shortcuts import render

from .constants import EPISODE_COUNT, EPISODE_DURATION_MINUTES, GENRES


def outline_page(request):
    return render(
        request,
        "studio/outline.html",
        {
            "genres": GENRES,
            "episode_count": EPISODE_COUNT,
            "episode_duration_minutes": EPISODE_DURATION_MINUTES,
        },
    )
```

Create `studio/templates/studio/base.html`:

```html
{% load static %}
<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}AI漫剧生成工具{% endblock %}</title>
</head>
<body>
  <main>
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

Create `studio/templates/studio/outline.html`:

```html
{% extends "studio/base.html" %}

{% block content %}
<h1>AI漫剧生成工具</h1>
<form method="post">
  {% csrf_token %}
  <label for="genre">题材/类型</label>
  <select id="genre" name="genre">
    {% for genre in genres %}
      <option value="{{ genre }}">{{ genre }}</option>
    {% endfor %}
  </select>
  <p>目标集数：{{ episode_count }} 集</p>
  <p>每集时长：{{ episode_duration_minutes }} 分钟</p>
</form>
{% endblock %}
```

Create `workspace/.gitkeep` as an empty file.

- [ ] **Step 5: Run the smoke test and verify it passes**

Install dependencies if they are not already available:

```bash
python -m pip install -r requirements.txt
```

Run:

```bash
pytest tests/test_project_smoke.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit the scaffold**

Run:

```bash
git add .gitignore .env.example requirements.txt pytest.ini manage.py aigc_site studio workspace/.gitkeep tests/test_project_smoke.py
git commit -m "feat: scaffold django studio app"
```

---

### Task 2: Local JSON Workspace Repository

**Files:**
- Create: `studio/repositories/__init__.py`
- Create: `studio/repositories/workspace.py`
- Test: `tests/test_workspace_repository.py`

- [ ] **Step 1: Write repository tests**

Create `tests/test_workspace_repository.py`:

```python
import json

import pytest

from studio.repositories.workspace import JsonWorkspaceRepository


def test_create_workspace_writes_default_structure(tmp_path):
    repo = JsonWorkspaceRepository(tmp_path)

    data = repo.create_workspace("逆袭爽文", workspace_id="fixed-id")

    saved_path = tmp_path / "fixed-id.json"
    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    assert data == saved
    assert data["id"] == "fixed-id"
    assert data["genre"] == "逆袭爽文"
    assert data["episode_count"] == 60
    assert data["episode_duration_minutes"] == 2
    assert data["outlines"] == []
    assert data["selected_outline_id"] is None
    assert data["script_plan"] == []
    assert data["episode_1_script"] == ""
    assert data["storyboard_prompts"] == []
    assert data["created_at"]
    assert data["updated_at"]


def test_update_workspace_preserves_unrelated_fields(tmp_path):
    repo = JsonWorkspaceRepository(tmp_path)
    repo.create_workspace("都市修真", workspace_id="story-1")

    first_update = repo.update_workspace(
        "story-1",
        outlines=[{"id": "outline-1", "title": "title"}],
        selected_outline_id="outline-1",
        episode_1_script="first script",
    )
    second_update = repo.update_workspace(
        "story-1",
        outlines=[{"id": "outline-2", "title": "new title"}],
        selected_outline_id=None,
    )

    assert first_update["episode_1_script"] == "first script"
    assert second_update["episode_1_script"] == "first script"
    assert second_update["outlines"] == [{"id": "outline-2", "title": "new title"}]
    assert second_update["selected_outline_id"] is None


def test_get_workspace_raises_for_missing_file(tmp_path):
    repo = JsonWorkspaceRepository(tmp_path)

    with pytest.raises(FileNotFoundError):
        repo.get_workspace("missing")


def test_create_workspace_rejects_unknown_genre(tmp_path):
    repo = JsonWorkspaceRepository(tmp_path)

    with pytest.raises(ValueError, match="Unknown genre"):
        repo.create_workspace("未知题材", workspace_id="bad")
```

- [ ] **Step 2: Run repository tests and verify they fail**

Run:

```bash
pytest tests/test_workspace_repository.py -q
```

Expected: FAIL because `studio.repositories.workspace` does not exist.

- [ ] **Step 3: Implement the repository**

Create `studio/repositories/__init__.py` as an empty file.

Create `studio/repositories/workspace.py`:

```python
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings

from studio.constants import EPISODE_COUNT, EPISODE_DURATION_MINUTES, GENRES


class JsonWorkspaceRepository:
    def __init__(self, workspace_dir=None):
        self.workspace_dir = Path(workspace_dir or settings.WORKSPACE_DIR)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def create_workspace(self, genre, workspace_id=None):
        if genre not in GENRES:
            raise ValueError(f"Unknown genre: {genre}")

        now = self._now()
        workspace = {
            "id": workspace_id or now.replace(":", "").replace("-", "").replace("+", "")[:15],
            "genre": genre,
            "episode_count": EPISODE_COUNT,
            "episode_duration_minutes": EPISODE_DURATION_MINUTES,
            "outlines": [],
            "selected_outline_id": None,
            "script_plan": [],
            "episode_1_script": "",
            "storyboard_prompts": [],
            "created_at": now,
            "updated_at": now,
        }
        return self.save_workspace(workspace)

    def get_workspace(self, workspace_id):
        path = self.path_for(workspace_id)
        if not path.exists():
            raise FileNotFoundError(f"Workspace not found: {workspace_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def save_workspace(self, workspace):
        workspace = dict(workspace)
        workspace["updated_at"] = self._now()
        self.path_for(workspace["id"]).write_text(
            json.dumps(workspace, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return workspace

    def update_workspace(self, workspace_id, **fields):
        workspace = self.get_workspace(workspace_id)
        workspace.update(fields)
        return self.save_workspace(workspace)

    def path_for(self, workspace_id):
        safe_id = str(workspace_id).replace("/", "").replace("\\", "")
        return self.workspace_dir / f"{safe_id}.json"

    @staticmethod
    def _now():
        return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
```

- [ ] **Step 4: Run repository tests and verify they pass**

Run:

```bash
pytest tests/test_workspace_repository.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the repository**

Run:

```bash
git add studio/repositories tests/test_workspace_repository.py
git commit -m "feat: add json workspace repository"
```

---

### Task 3: OpenAI-Compatible LLM Provider

**Files:**
- Create: `studio/llm/__init__.py`
- Create: `studio/llm/provider.py`
- Test: `tests/test_llm_provider.py`

- [ ] **Step 1: Write provider tests**

Create `tests/test_llm_provider.py`:

```python
import json

import httpx
import pytest

from studio.llm.provider import (
    LLMAPIError,
    LLMConfig,
    LLMConfigurationError,
    LLMJSONParseError,
    LLMProvider,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.test/v1/chat/completions")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("api failed", request=request, response=response)


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self.response


def test_config_from_env_requires_all_values():
    with pytest.raises(LLMConfigurationError):
        LLMConfig.from_env({})


def test_generate_text_calls_chat_completions():
    response = FakeResponse(
        payload={"choices": [{"message": {"content": "hello"}}]},
    )
    client = FakeClient(response)
    provider = LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"),
        client=client,
    )

    text = provider.generate_text([{"role": "user", "content": "Hi"}], temperature=0.2)

    assert text == "hello"
    assert client.calls[0]["url"] == "https://example.test/v1/chat/completions"
    assert client.calls[0]["headers"]["Authorization"] == "Bearer key"
    assert client.calls[0]["json"]["model"] == "model-a"
    assert client.calls[0]["json"]["temperature"] == 0.2


def test_generate_json_parses_text_response():
    response = FakeResponse(
        payload={"choices": [{"message": {"content": "{\"ok\": true}"}}]},
    )
    provider = LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"),
        client=FakeClient(response),
    )

    assert provider.generate_json([{"role": "user", "content": "Return JSON"}]) == {"ok": True}


def test_generate_json_raises_for_invalid_json():
    response = FakeResponse(payload={"choices": [{"message": {"content": "not-json"}}]})
    provider = LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"),
        client=FakeClient(response),
    )

    with pytest.raises(LLMJSONParseError):
        provider.generate_json([{"role": "user", "content": "Return JSON"}])


def test_generate_text_wraps_http_errors():
    response = FakeResponse(status_code=401, payload={"error": "bad key"})
    provider = LLMProvider(
        LLMConfig(base_url="https://example.test/v1", api_key="key", model="model-a"),
        client=FakeClient(response),
    )

    with pytest.raises(LLMAPIError):
        provider.generate_text([{"role": "user", "content": "Hi"}])
```

- [ ] **Step 2: Run provider tests and verify they fail**

Run:

```bash
pytest tests/test_llm_provider.py -q
```

Expected: FAIL because `studio.llm.provider` does not exist.

- [ ] **Step 3: Implement the provider**

Create `studio/llm/__init__.py` as an empty file.

Create `studio/llm/provider.py`:

```python
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
        environ = environ or os.environ
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
    def from_env(cls):
        return cls(LLMConfig.from_env())

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
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMAPIError("Model response did not include message content") from exc

    def generate_json(self, messages, temperature=None):
        raw_text = self.generate_text(messages, temperature=temperature)
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise LLMJSONParseError(raw_text) from exc
```

- [ ] **Step 4: Run provider tests and verify they pass**

Run:

```bash
pytest tests/test_llm_provider.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the provider**

Run:

```bash
git add studio/llm tests/test_llm_provider.py
git commit -m "feat: add openai compatible llm provider"
```

---

### Task 4: Outline Generation Service

**Files:**
- Create: `studio/services/__init__.py`
- Create: `studio/services/outline.py`
- Test: `tests/test_outline_service.py`

- [ ] **Step 1: Write outline service tests**

Create `tests/test_outline_service.py`:

```python
import pytest

from studio.services.outline import generate_outlines, validate_outlines


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload
        self.messages = None

    def generate_json(self, messages, temperature=None):
        self.messages = messages
        self.temperature = temperature
        return self.payload


def make_outline(index):
    return {
        "id": f"outline-{index}",
        "title": f"标题 {index}",
        "core_premise": f"核心设定 {index}",
        "protagonist": f"主角 {index}",
        "hook": f"爽点 {index}",
        "arc_summary": f"60集走向 {index}",
    }


def test_generate_outlines_returns_six_candidates():
    provider = FakeProvider({"outlines": [make_outline(i) for i in range(1, 7)]})

    outlines = generate_outlines(provider, "逆袭爽文")

    assert len(outlines) == 6
    assert outlines[0]["id"] == "outline-1"
    assert "逆袭爽文" in provider.messages[-1]["content"]
    assert "60集" in provider.messages[-1]["content"]
    assert provider.temperature == 0.9


def test_validate_outlines_rejects_wrong_count():
    with pytest.raises(ValueError, match="6"):
        validate_outlines({"outlines": [make_outline(1)]})


def test_validate_outlines_rejects_missing_field():
    bad_outline = make_outline(1)
    del bad_outline["hook"]

    with pytest.raises(ValueError, match="hook"):
        validate_outlines({"outlines": [bad_outline for _ in range(6)]})
```

- [ ] **Step 2: Run outline service tests and verify they fail**

Run:

```bash
pytest tests/test_outline_service.py -q
```

Expected: FAIL because `studio.services.outline` does not exist.

- [ ] **Step 3: Implement outline service**

Create `studio/services/__init__.py` as an empty file.

Create `studio/services/outline.py`:

```python
from studio.constants import EPISODE_COUNT, EPISODE_DURATION_MINUTES, OUTLINE_CANDIDATE_COUNT


REQUIRED_OUTLINE_FIELDS = {
    "id",
    "title",
    "core_premise",
    "protagonist",
    "hook",
    "arc_summary",
}


def generate_outlines(provider, genre):
    payload = provider.generate_json(
        [
            {
                "role": "system",
                "content": (
                    "你是爆款AI漫剧策划。只返回JSON，不要返回Markdown。"
                    "JSON格式为 {\"outlines\": [...]}。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"题材：{genre}\n"
                    f"目标：生成{OUTLINE_CANDIDATE_COUNT}个AI漫剧大纲候选。\n"
                    f"固定规格：{EPISODE_COUNT}集，每集{EPISODE_DURATION_MINUTES}分钟。\n"
                    "每个候选必须包含 id、title、core_premise、protagonist、hook、arc_summary。"
                    "核心设定由你随机生成，要适合短视频漫剧。"
                ),
            },
        ],
        temperature=0.9,
    )
    return validate_outlines(payload)


def validate_outlines(payload):
    outlines = payload.get("outlines") if isinstance(payload, dict) else None
    if not isinstance(outlines, list):
        raise ValueError("Model response must include an outlines list")
    if len(outlines) != OUTLINE_CANDIDATE_COUNT:
        raise ValueError(f"Expected {OUTLINE_CANDIDATE_COUNT} outlines")

    for index, outline in enumerate(outlines, start=1):
        if not isinstance(outline, dict):
            raise ValueError(f"Outline {index} must be an object")
        missing = REQUIRED_OUTLINE_FIELDS - set(outline)
        if missing:
            raise ValueError(f"Outline {index} missing fields: {', '.join(sorted(missing))}")
    return outlines
```

- [ ] **Step 4: Run outline service tests and verify they pass**

Run:

```bash
pytest tests/test_outline_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit outline service**

Run:

```bash
git add studio/services tests/test_outline_service.py
git commit -m "feat: add outline generation service"
```

---

### Task 5: Script Generation Service

**Files:**
- Create: `studio/services/script.py`
- Test: `tests/test_script_service.py`

- [ ] **Step 1: Write script service tests**

Create `tests/test_script_service.py`:

```python
import pytest

from studio.services.script import generate_script, validate_script_payload


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload
        self.messages = None

    def generate_json(self, messages, temperature=None):
        self.messages = messages
        self.temperature = temperature
        return self.payload


def make_episode(index):
    return {
        "episode": index,
        "title": f"第{index}集",
        "summary": f"剧情摘要 {index}",
        "key_conflict": f"核心冲突 {index}",
        "cliffhanger": f"钩子 {index}",
    }


def make_outline():
    return {
        "id": "outline-1",
        "title": "重生归来",
        "core_premise": "主角重生后逆袭",
        "protagonist": "林霄",
        "hook": "开局被背叛，下一秒重生",
        "arc_summary": "从底层逆袭到巅峰",
    }


def test_generate_script_returns_plan_and_episode_1_script():
    payload = {
        "script_plan": [make_episode(i) for i in range(1, 61)],
        "episode_1_script": "第一集完整剧本",
    }
    provider = FakeProvider(payload)

    result = generate_script(provider, make_outline())

    assert len(result["script_plan"]) == 60
    assert result["episode_1_script"] == "第一集完整剧本"
    assert "重生归来" in provider.messages[-1]["content"]
    assert provider.temperature == 0.7


def test_validate_script_payload_rejects_wrong_episode_count():
    with pytest.raises(ValueError, match="60"):
        validate_script_payload({"script_plan": [make_episode(1)], "episode_1_script": "script"})


def test_validate_script_payload_rejects_missing_episode_script():
    with pytest.raises(ValueError, match="episode_1_script"):
        validate_script_payload({"script_plan": [make_episode(i) for i in range(1, 61)]})
```

- [ ] **Step 2: Run script service tests and verify they fail**

Run:

```bash
pytest tests/test_script_service.py -q
```

Expected: FAIL because `studio.services.script` does not exist.

- [ ] **Step 3: Implement script service**

Create `studio/services/script.py`:

```python
from studio.constants import EPISODE_COUNT, EPISODE_DURATION_MINUTES


REQUIRED_EPISODE_FIELDS = {
    "episode",
    "title",
    "summary",
    "key_conflict",
    "cliffhanger",
}


def generate_script(provider, outline):
    payload = provider.generate_json(
        [
            {
                "role": "system",
                "content": (
                    "你是AI漫剧编剧。只返回JSON，不要返回Markdown。"
                    "JSON格式为 {\"script_plan\": [...], \"episode_1_script\": \"...\"}。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请基于以下大纲生成剧本加工结果。\n"
                    f"标题：{outline['title']}\n"
                    f"核心设定：{outline['core_premise']}\n"
                    f"主角：{outline['protagonist']}\n"
                    f"爽点/钩子：{outline['hook']}\n"
                    f"整体走向：{outline['arc_summary']}\n"
                    f"固定规格：{EPISODE_COUNT}集，每集{EPISODE_DURATION_MINUTES}分钟。\n"
                    "输出60集分集剧本大纲，每集包含 episode、title、summary、key_conflict、cliffhanger。"
                    "同时输出第1集完整剧本样稿 episode_1_script。"
                ),
            },
        ],
        temperature=0.7,
    )
    return validate_script_payload(payload)


def validate_script_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("Script response must be an object")
    script_plan = payload.get("script_plan")
    episode_1_script = payload.get("episode_1_script")
    if not isinstance(script_plan, list):
        raise ValueError("script_plan must be a list")
    if len(script_plan) != EPISODE_COUNT:
        raise ValueError(f"Expected {EPISODE_COUNT} script plan entries")
    if not isinstance(episode_1_script, str) or not episode_1_script.strip():
        raise ValueError("episode_1_script must be a non-empty string")

    for index, episode in enumerate(script_plan, start=1):
        if not isinstance(episode, dict):
            raise ValueError(f"Episode {index} must be an object")
        missing = REQUIRED_EPISODE_FIELDS - set(episode)
        if missing:
            raise ValueError(f"Episode {index} missing fields: {', '.join(sorted(missing))}")
        if episode["episode"] != index:
            raise ValueError(f"Episode number must be {index}")

    return {
        "script_plan": script_plan,
        "episode_1_script": episode_1_script,
    }
```

- [ ] **Step 4: Run script service tests and verify they pass**

Run:

```bash
pytest tests/test_script_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit script service**

Run:

```bash
git add studio/services/script.py tests/test_script_service.py
git commit -m "feat: add script generation service"
```

---

### Task 6: Storyboard Prompt Service

**Files:**
- Create: `studio/services/storyboard.py`
- Test: `tests/test_storyboard_service.py`

- [ ] **Step 1: Write storyboard service tests**

Create `tests/test_storyboard_service.py`:

```python
import pytest

from studio.services.storyboard import generate_storyboard, validate_storyboard_payload


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload
        self.messages = None

    def generate_json(self, messages, temperature=None):
        self.messages = messages
        self.temperature = temperature
        return self.payload


def make_shot(index):
    return {
        "shot_number": index,
        "duration": "6秒",
        "visual_description": f"画面 {index}",
        "character_action": f"动作 {index}",
        "dialogue_or_narration": f"台词 {index}",
        "camera_language": f"镜头 {index}",
        "image_prompt": f"绘图prompt {index}",
        "video_prompt": f"视频prompt {index}",
    }


def test_generate_storyboard_returns_valid_shots():
    provider = FakeProvider({"storyboard_prompts": [make_shot(i) for i in range(1, 13)]})

    result = generate_storyboard(provider, "第一集完整剧本")

    assert len(result) == 12
    assert result[0]["shot_number"] == 1
    assert "第一集完整剧本" in provider.messages[-1]["content"]
    assert provider.temperature == 0.6


def test_validate_storyboard_payload_rejects_too_few_shots():
    with pytest.raises(ValueError, match="12"):
        validate_storyboard_payload({"storyboard_prompts": [make_shot(1)]})


def test_validate_storyboard_payload_rejects_missing_prompt_field():
    shot = make_shot(1)
    del shot["video_prompt"]

    with pytest.raises(ValueError, match="video_prompt"):
        validate_storyboard_payload({"storyboard_prompts": [shot for _ in range(12)]})
```

- [ ] **Step 2: Run storyboard service tests and verify they fail**

Run:

```bash
pytest tests/test_storyboard_service.py -q
```

Expected: FAIL because `studio.services.storyboard` does not exist.

- [ ] **Step 3: Implement storyboard service**

Create `studio/services/storyboard.py`:

```python
from studio.constants import MAX_STORYBOARD_SHOTS, MIN_STORYBOARD_SHOTS


REQUIRED_STORYBOARD_FIELDS = {
    "shot_number",
    "duration",
    "visual_description",
    "character_action",
    "dialogue_or_narration",
    "camera_language",
    "image_prompt",
    "video_prompt",
}


def generate_storyboard(provider, episode_1_script):
    payload = provider.generate_json(
        [
            {
                "role": "system",
                "content": (
                    "你是AI漫剧分镜导演。只返回JSON，不要返回Markdown。"
                    "JSON格式为 {\"storyboard_prompts\": [...]}。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请把以下2分钟第1集剧本拆成12到20个分镜。\n"
                    "每个分镜包含 shot_number、duration、visual_description、"
                    "character_action、dialogue_or_narration、camera_language、"
                    "image_prompt、video_prompt。\n"
                    f"剧本：\n{episode_1_script}"
                ),
            },
        ],
        temperature=0.6,
    )
    return validate_storyboard_payload(payload)


def validate_storyboard_payload(payload):
    prompts = payload.get("storyboard_prompts") if isinstance(payload, dict) else None
    if not isinstance(prompts, list):
        raise ValueError("storyboard_prompts must be a list")
    if not MIN_STORYBOARD_SHOTS <= len(prompts) <= MAX_STORYBOARD_SHOTS:
        raise ValueError(f"Expected {MIN_STORYBOARD_SHOTS} to {MAX_STORYBOARD_SHOTS} storyboard shots")

    for index, shot in enumerate(prompts, start=1):
        if not isinstance(shot, dict):
            raise ValueError(f"Shot {index} must be an object")
        missing = REQUIRED_STORYBOARD_FIELDS - set(shot)
        if missing:
            raise ValueError(f"Shot {index} missing fields: {', '.join(sorted(missing))}")
    return prompts
```

- [ ] **Step 4: Run storyboard service tests and verify they pass**

Run:

```bash
pytest tests/test_storyboard_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit storyboard service**

Run:

```bash
git add studio/services/storyboard.py tests/test_storyboard_service.py
git commit -m "feat: add storyboard prompt service"
```

---

### Task 7: Workflow Views and Routes

**Files:**
- Modify: `studio/urls.py`
- Modify: `studio/views.py`
- Test: `tests/test_views.py`

- [ ] **Step 1: Write view workflow tests**

Create `tests/test_views.py`:

```python
from django.urls import reverse

from studio.repositories.workspace import JsonWorkspaceRepository


def outline_payload():
    return [
        {
            "id": "outline-1",
            "title": "标题",
            "core_premise": "核心设定",
            "protagonist": "主角",
            "hook": "爽点",
            "arc_summary": "走向",
        }
        for _ in range(6)
    ]


def script_payload():
    return {
        "script_plan": [
            {
                "episode": i,
                "title": f"第{i}集",
                "summary": "摘要",
                "key_conflict": "冲突",
                "cliffhanger": "钩子",
            }
            for i in range(1, 61)
        ],
        "episode_1_script": "第一集完整剧本",
    }


def storyboard_payload():
    return [
        {
            "shot_number": i,
            "duration": "6秒",
            "visual_description": "画面",
            "character_action": "动作",
            "dialogue_or_narration": "台词",
            "camera_language": "镜头",
            "image_prompt": "绘图",
            "video_prompt": "视频",
        }
        for i in range(1, 13)
    ]


def test_generate_outlines_writes_workspace(settings, tmp_path, client, monkeypatch):
    settings.WORKSPACE_DIR = tmp_path
    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: object())
    monkeypatch.setattr("studio.views.generate_outlines", lambda provider, genre: outline_payload())

    response = client.post(reverse("studio:generate_outlines"), {"genre": "逆袭爽文"})

    assert response.status_code == 200
    assert "标题" in response.content.decode("utf-8")
    workspace_id = response.context["workspace"]["id"]
    saved = JsonWorkspaceRepository(tmp_path).get_workspace(workspace_id)
    assert saved["genre"] == "逆袭爽文"
    assert len(saved["outlines"]) == 6
    assert saved["selected_outline_id"] is None


def test_select_outline_redirects_to_script_page(settings, tmp_path, client):
    settings.WORKSPACE_DIR = tmp_path
    repo = JsonWorkspaceRepository(tmp_path)
    workspace = repo.create_workspace("逆袭爽文", workspace_id="story-1")
    repo.update_workspace(workspace["id"], outlines=outline_payload())

    response = client.post(
        reverse("studio:select_outline"),
        {"workspace_id": "story-1", "outline_id": "outline-1"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("studio:script", args=["story-1"])
    assert repo.get_workspace("story-1")["selected_outline_id"] == "outline-1"


def test_generate_script_writes_plan_and_episode_script(settings, tmp_path, client, monkeypatch):
    settings.WORKSPACE_DIR = tmp_path
    repo = JsonWorkspaceRepository(tmp_path)
    workspace = repo.create_workspace("逆袭爽文", workspace_id="story-1")
    repo.update_workspace(workspace["id"], outlines=outline_payload(), selected_outline_id="outline-1")
    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: object())
    monkeypatch.setattr("studio.views.generate_script", lambda provider, outline: script_payload())

    response = client.post(reverse("studio:generate_script", args=["story-1"]))

    assert response.status_code == 200
    saved = repo.get_workspace("story-1")
    assert len(saved["script_plan"]) == 60
    assert saved["episode_1_script"] == "第一集完整剧本"


def test_generate_storyboard_writes_prompts(settings, tmp_path, client, monkeypatch):
    settings.WORKSPACE_DIR = tmp_path
    repo = JsonWorkspaceRepository(tmp_path)
    workspace = repo.create_workspace("逆袭爽文", workspace_id="story-1")
    repo.update_workspace(workspace["id"], episode_1_script="第一集完整剧本")
    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: object())
    monkeypatch.setattr("studio.views.generate_storyboard", lambda provider, script: storyboard_payload())

    response = client.post(reverse("studio:generate_storyboard", args=["story-1"]))

    assert response.status_code == 200
    saved = repo.get_workspace("story-1")
    assert len(saved["storyboard_prompts"]) == 12


def test_generation_error_keeps_previous_content(settings, tmp_path, client, monkeypatch):
    settings.WORKSPACE_DIR = tmp_path
    repo = JsonWorkspaceRepository(tmp_path)
    workspace = repo.create_workspace("逆袭爽文", workspace_id="story-1")
    repo.update_workspace(workspace["id"], episode_1_script="previous")
    monkeypatch.setattr("studio.views.LLMProvider.from_env", lambda: object())
    monkeypatch.setattr(
        "studio.views.generate_storyboard",
        lambda provider, script: (_ for _ in ()).throw(ValueError("bad model response")),
    )

    response = client.post(reverse("studio:generate_storyboard", args=["story-1"]))

    assert response.status_code == 200
    assert "bad model response" in response.content.decode("utf-8")
    assert repo.get_workspace("story-1")["episode_1_script"] == "previous"
```

- [ ] **Step 2: Run view tests and verify they fail**

Run:

```bash
pytest tests/test_views.py -q
```

Expected: FAIL because the routes and workflow views are not implemented.

- [ ] **Step 3: Add workflow routes**

Replace `studio/urls.py` with:

```python
from django.urls import path

from . import views


app_name = "studio"

urlpatterns = [
    path("", views.outline_page, name="outline"),
    path("outlines/generate/", views.generate_outlines_view, name="generate_outlines"),
    path("outlines/select/", views.select_outline_view, name="select_outline"),
    path("script/<str:workspace_id>/", views.script_page, name="script"),
    path("script/<str:workspace_id>/generate/", views.generate_script_view, name="generate_script"),
    path("storyboard/<str:workspace_id>/", views.storyboard_page, name="storyboard"),
    path("storyboard/<str:workspace_id>/generate/", views.generate_storyboard_view, name="generate_storyboard"),
]
```

- [ ] **Step 4: Implement workflow views**

Replace `studio/views.py` with:

```python
from django.shortcuts import redirect, render
from django.urls import reverse

from studio.constants import EPISODE_COUNT, EPISODE_DURATION_MINUTES, GENRES
from studio.llm.provider import LLMProvider
from studio.repositories.workspace import JsonWorkspaceRepository
from studio.services.outline import generate_outlines
from studio.services.script import generate_script
from studio.services.storyboard import generate_storyboard


def outline_page(request):
    return _render_outline(request)


def generate_outlines_view(request):
    genre = request.POST.get("genre", "")
    repo = JsonWorkspaceRepository()
    workspace = None
    try:
        workspace = repo.create_workspace(genre)
        outlines = generate_outlines(LLMProvider.from_env(), genre)
        workspace = repo.update_workspace(workspace["id"], outlines=outlines, selected_outline_id=None)
    except Exception as exc:
        return _render_outline(request, workspace=workspace, selected_genre=genre, error=str(exc))
    return _render_outline(request, workspace=workspace, selected_genre=genre)


def select_outline_view(request):
    workspace_id = request.POST["workspace_id"]
    outline_id = request.POST["outline_id"]
    repo = JsonWorkspaceRepository()
    workspace = repo.get_workspace(workspace_id)
    valid_outline_ids = {outline["id"] for outline in workspace["outlines"]}
    if outline_id not in valid_outline_ids:
        return _render_outline(request, workspace=workspace, selected_genre=workspace["genre"], error="Selected outline was not found")
    repo.update_workspace(workspace_id, selected_outline_id=outline_id)
    return redirect(reverse("studio:script", args=[workspace_id]))


def script_page(request, workspace_id):
    workspace = JsonWorkspaceRepository().get_workspace(workspace_id)
    return render(request, "studio/script.html", {"workspace": workspace, "selected_outline": _selected_outline(workspace)})


def generate_script_view(request, workspace_id):
    repo = JsonWorkspaceRepository()
    workspace = repo.get_workspace(workspace_id)
    selected_outline = _selected_outline(workspace)
    try:
        if not selected_outline:
            raise ValueError("No outline selected")
        result = generate_script(LLMProvider.from_env(), selected_outline)
        workspace = repo.update_workspace(
            workspace_id,
            script_plan=result["script_plan"],
            episode_1_script=result["episode_1_script"],
        )
    except Exception as exc:
        return render(
            request,
            "studio/script.html",
            {"workspace": workspace, "selected_outline": selected_outline, "error": str(exc)},
        )
    return render(request, "studio/script.html", {"workspace": workspace, "selected_outline": selected_outline})


def storyboard_page(request, workspace_id):
    workspace = JsonWorkspaceRepository().get_workspace(workspace_id)
    return render(request, "studio/storyboard.html", {"workspace": workspace})


def generate_storyboard_view(request, workspace_id):
    repo = JsonWorkspaceRepository()
    workspace = repo.get_workspace(workspace_id)
    try:
        if not workspace.get("episode_1_script"):
            raise ValueError("Episode 1 script is empty")
        storyboard_prompts = generate_storyboard(LLMProvider.from_env(), workspace["episode_1_script"])
        workspace = repo.update_workspace(workspace_id, storyboard_prompts=storyboard_prompts)
    except Exception as exc:
        return render(request, "studio/storyboard.html", {"workspace": workspace, "error": str(exc)})
    return render(request, "studio/storyboard.html", {"workspace": workspace})


def _render_outline(request, workspace=None, selected_genre=None, error=None):
    return render(
        request,
        "studio/outline.html",
        {
            "genres": GENRES,
            "selected_genre": selected_genre or (workspace or {}).get("genre") or GENRES[0],
            "episode_count": EPISODE_COUNT,
            "episode_duration_minutes": EPISODE_DURATION_MINUTES,
            "workspace": workspace,
            "error": error,
        },
    )


def _selected_outline(workspace):
    selected_id = workspace.get("selected_outline_id")
    for outline in workspace.get("outlines", []):
        if outline["id"] == selected_id:
            return outline
    return None
```

- [ ] **Step 5: Run view tests and verify remaining failures are template-related**

Run:

```bash
pytest tests/test_views.py -q
```

Expected: FAIL if `script.html` or `storyboard.html` is missing. If the only failures are missing templates, continue to Task 8.

- [ ] **Step 6: Leave route and view changes uncommitted for Task 8**

Do not commit yet. Task 8 adds the templates required by these views, then commits the complete page workflow together.

---

### Task 8: Templates, Styling, and Submit Guard

**Files:**
- Modify: `studio/templates/studio/base.html`
- Modify: `studio/templates/studio/outline.html`
- Create: `studio/templates/studio/script.html`
- Create: `studio/templates/studio/storyboard.html`
- Create: `studio/static/studio/app.css`
- Create: `studio/static/studio/app.js`
- Test: `tests/test_project_smoke.py`
- Test: `tests/test_views.py`

- [ ] **Step 1: Expand the smoke test for page controls**

Replace `tests/test_project_smoke.py` with:

```python
from django.urls import reverse


def test_outline_page_renders(client):
    response = client.get(reverse("studio:outline"))
    html = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "AI漫剧生成工具" in html
    assert "逆袭爽文" in html
    assert "刷新大纲" in html
```

- [ ] **Step 2: Run page tests and verify they fail on missing UI text**

Run:

```bash
pytest tests/test_project_smoke.py tests/test_views.py -q
```

Expected: FAIL until templates contain the workflow UI.

- [ ] **Step 3: Replace the base template**

Replace `studio/templates/studio/base.html` with:

```html
{% load static %}
<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}AI漫剧生成工具{% endblock %}</title>
  <link rel="stylesheet" href="{% static 'studio/app.css' %}">
  <script defer src="{% static 'studio/app.js' %}"></script>
</head>
<body>
  <main class="shell">
    {% if error %}
      <div class="alert" role="alert">{{ error }}</div>
    {% endif %}
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

- [ ] **Step 4: Replace the outline template**

Replace `studio/templates/studio/outline.html` with:

```html
{% extends "studio/base.html" %}

{% block content %}
<section class="toolbar">
  <div>
    <p class="eyebrow">AI Comic Drama Studio</p>
    <h1>AI漫剧生成工具</h1>
    <p class="subtitle">选择热门题材，随机生成核心设定和一批可筛选的大纲。</p>
  </div>
  <form method="post" action="{% url 'studio:generate_outlines' %}" data-submit-guard>
    {% csrf_token %}
    <select name="genre" aria-label="题材/类型">
      {% for genre in genres %}
        <option value="{{ genre }}" {% if genre == selected_genre %}selected{% endif %}>{{ genre }}</option>
      {% endfor %}
    </select>
    <button type="submit">刷新大纲</button>
  </form>
</section>

<section class="meta-grid">
  <div>
    <span>目标集数</span>
    <strong>{{ episode_count }} 集</strong>
  </div>
  <div>
    <span>单集时长</span>
    <strong>{{ episode_duration_minutes }} 分钟</strong>
  </div>
  <div>
    <span>核心设定</span>
    <strong>AI随机生成</strong>
  </div>
</section>

{% if workspace and workspace.outlines %}
  <form method="post" action="{% url 'studio:select_outline' %}" data-submit-guard>
    {% csrf_token %}
    <input type="hidden" name="workspace_id" value="{{ workspace.id }}">
    <div class="card-grid">
      {% for outline in workspace.outlines %}
        <label class="outline-card">
          <input type="radio" name="outline_id" value="{{ outline.id }}" required>
          <span class="card-title">{{ outline.title }}</span>
          <span class="card-line">主角：{{ outline.protagonist }}</span>
          <span class="card-line">设定：{{ outline.core_premise }}</span>
          <span class="card-line">爽点：{{ outline.hook }}</span>
          <span class="card-line">走向：{{ outline.arc_summary }}</span>
        </label>
      {% endfor %}
    </div>
    <div class="actions">
      <button type="submit">AI加工</button>
    </div>
  </form>
{% else %}
  <section class="empty">
    <h2>还没有大纲候选</h2>
    <p>选择题材后点击右上角刷新大纲。</p>
  </section>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Create script and storyboard templates**

Create `studio/templates/studio/script.html`:

```html
{% extends "studio/base.html" %}

{% block title %}剧本加工 - AI漫剧生成工具{% endblock %}

{% block content %}
<section class="toolbar">
  <div>
    <p class="eyebrow">Step 2</p>
    <h1>剧本加工</h1>
    {% if selected_outline %}
      <p class="subtitle">{{ selected_outline.title }} / {{ selected_outline.protagonist }}</p>
    {% endif %}
  </div>
  <form method="post" action="{% url 'studio:generate_script' workspace.id %}" data-submit-guard>
    {% csrf_token %}
    <button type="submit">生成剧本</button>
  </form>
</section>

{% if selected_outline %}
  <section class="panel">
    <h2>选中大纲</h2>
    <p>{{ selected_outline.core_premise }}</p>
    <p>{{ selected_outline.hook }}</p>
  </section>
{% endif %}

{% if workspace.script_plan %}
  <section class="panel">
    <div class="section-head">
      <h2>60集分集剧本大纲</h2>
      <a class="button-link" href="{% url 'studio:storyboard' workspace.id %}">生成分镜 Prompt</a>
    </div>
    <div class="episode-list">
      {% for episode in workspace.script_plan %}
        <article>
          <h3>第{{ episode.episode }}集：{{ episode.title }}</h3>
          <p>{{ episode.summary }}</p>
          <p><strong>冲突：</strong>{{ episode.key_conflict }}</p>
          <p><strong>钩子：</strong>{{ episode.cliffhanger }}</p>
        </article>
      {% endfor %}
    </div>
  </section>
{% endif %}

{% if workspace.episode_1_script %}
  <section class="panel">
    <h2>第1集完整剧本样稿</h2>
    <pre>{{ workspace.episode_1_script }}</pre>
  </section>
{% endif %}
{% endblock %}
```

Create `studio/templates/studio/storyboard.html`:

```html
{% extends "studio/base.html" %}

{% block title %}分镜 Prompt - AI漫剧生成工具{% endblock %}

{% block content %}
<section class="toolbar">
  <div>
    <p class="eyebrow">Step 3</p>
    <h1>分镜 Prompt</h1>
    <p class="subtitle">基于第1集完整剧本生成12到20个分镜。</p>
  </div>
  <form method="post" action="{% url 'studio:generate_storyboard' workspace.id %}" data-submit-guard>
    {% csrf_token %}
    <button type="submit">生成分镜 Prompt</button>
  </form>
</section>

{% if workspace.storyboard_prompts %}
  <section class="shot-list">
    {% for shot in workspace.storyboard_prompts %}
      <article class="panel">
        <h2>镜头 {{ shot.shot_number }} / {{ shot.duration }}</h2>
        <p><strong>画面：</strong>{{ shot.visual_description }}</p>
        <p><strong>动作：</strong>{{ shot.character_action }}</p>
        <p><strong>台词/旁白：</strong>{{ shot.dialogue_or_narration }}</p>
        <p><strong>镜头语言：</strong>{{ shot.camera_language }}</p>
        <p><strong>AI绘图 Prompt：</strong>{{ shot.image_prompt }}</p>
        <p><strong>AI视频 Prompt：</strong>{{ shot.video_prompt }}</p>
      </article>
    {% endfor %}
  </section>
{% else %}
  <section class="empty">
    <h2>还没有分镜 Prompt</h2>
    <p>确认第1集剧本后点击生成。</p>
  </section>
{% endif %}
{% endblock %}
```

- [ ] **Step 6: Add styling and submit guard**

Create `studio/static/studio/app.css`:

```css
:root {
  color-scheme: light;
  font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
  background: #f6f7f9;
  color: #1f2937;
}

body {
  margin: 0;
}

button,
select {
  font: inherit;
}

button,
.button-link {
  border: 0;
  border-radius: 6px;
  background: #2563eb;
  color: #fff;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 40px;
  padding: 0 16px;
  text-decoration: none;
}

button[disabled] {
  cursor: wait;
  opacity: 0.65;
}

select {
  min-height: 40px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  padding: 0 12px;
  background: #fff;
}

.shell {
  max-width: 1180px;
  margin: 0 auto;
  padding: 32px 20px 56px;
}

.toolbar,
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.toolbar form {
  display: flex;
  gap: 10px;
}

.eyebrow {
  margin: 0 0 6px;
  color: #2563eb;
  font-size: 13px;
  font-weight: 700;
}

h1 {
  margin: 0;
  font-size: 32px;
}

.subtitle {
  margin: 8px 0 0;
  color: #6b7280;
}

.alert {
  margin-bottom: 20px;
  border: 1px solid #f59e0b;
  border-radius: 6px;
  background: #fffbeb;
  padding: 12px 14px;
  color: #92400e;
}

.meta-grid,
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
  margin-top: 24px;
}

.meta-grid div,
.outline-card,
.panel,
.empty {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  padding: 18px;
}

.meta-grid span,
.card-line {
  display: block;
  color: #6b7280;
  font-size: 14px;
}

.meta-grid strong,
.card-title {
  display: block;
  margin-top: 6px;
  color: #111827;
  font-size: 18px;
}

.outline-card {
  cursor: pointer;
}

.outline-card input {
  margin-right: 8px;
}

.actions {
  margin-top: 20px;
  text-align: right;
}

.panel,
.empty,
.shot-list {
  margin-top: 24px;
}

.episode-list {
  display: grid;
  gap: 12px;
  max-height: 520px;
  overflow: auto;
}

.episode-list article {
  border-top: 1px solid #e5e7eb;
  padding-top: 12px;
}

pre {
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 720px) {
  .toolbar,
  .section-head,
  .toolbar form {
    align-items: stretch;
    flex-direction: column;
  }
}
```

Create `studio/static/studio/app.js`:

```javascript
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!form.matches("[data-submit-guard]")) {
    return;
  }
  const button = form.querySelector("button[type='submit']");
  if (!button) {
    return;
  }
  button.disabled = true;
  button.dataset.originalText = button.textContent;
  button.textContent = "生成中...";
});
```

- [ ] **Step 7: Run page and view tests and verify they pass**

Run:

```bash
pytest tests/test_project_smoke.py tests/test_views.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit templates and view workflow**

Run:

```bash
git add studio/templates studio/static tests/test_project_smoke.py tests/test_views.py studio/urls.py studio/views.py
git commit -m "feat: add studio workflow pages"
```

---

### Task 9: README and Full Verification

**Files:**
- Create: `README.md`
- Modify: no application code unless verification exposes a bug

- [ ] **Step 1: Write README with setup and smoke instructions**

Create `README.md`:

```markdown
# AI漫剧生成工具

本项目是一个本地单用户 Django Web 工具，用于通过 OpenAI-compatible 大模型接口生成 AI 漫剧大纲、剧本和分镜 Prompt。

## 本地启动

1. 创建虚拟环境并安装依赖：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

2. 复制 `.env.example` 为 `.env`，填写模型配置：

```dotenv
LLM_BASE_URL=https://api.example.com/v1
LLM_API_KEY=replace-with-your-key
LLM_MODEL=replace-with-model-name
```

3. 启动 Django：

```bash
python manage.py runserver
```

4. 打开 `http://127.0.0.1:8000/`，按顺序完成：

- 选择题材并刷新大纲。
- 选择一个大纲并点击 AI加工。
- 生成剧本。
- 生成分镜 Prompt。

## 测试

```bash
pytest -q
```

## 本地数据

生成过程写入 `workspace/*.json`。这些文件用于本地创作状态，不提交到 git。
```

- [ ] **Step 2: Run the full test suite**

Run:

```bash
pytest -q
```

Expected: PASS for all tests.

- [ ] **Step 3: Run Django system checks**

Run:

```bash
python manage.py check
```

Expected: `System check identified no issues`.

- [ ] **Step 4: Verify git ignores generated workspace files**

Run in PowerShell:

```bash
python -c "from pathlib import Path; Path('workspace/manual-check.json').write_text('{}', encoding='utf-8')"
git status --short
```

Expected: `workspace/manual-check.json` does not appear in `git status --short`.

Remove the manual check file:

```bash
python -c "from pathlib import Path; Path('workspace/manual-check.json').unlink(missing_ok=True)"
```

- [ ] **Step 5: Commit README and final verification updates**

Run:

```bash
git add README.md
git commit -m "docs: add local setup guide"
```

- [ ] **Step 6: Confirm final status**

Run:

```bash
git status --short
```

Expected: no output.

---

## Self-Review

- Spec coverage:
  - Django app scaffold: Task 1.
  - OpenAI-compatible `.env` model config: Task 3 and Task 9.
  - Local JSON workspace files: Task 2.
  - Six outline candidates and genre dropdown: Tasks 1, 4, and 8.
  - AI加工 script generation: Tasks 5, 7, and 8.
  - Episode 1 storyboard prompts: Tasks 6, 7, and 8.
  - Error display without overwriting successful content: Task 7.
  - Focused tests for provider, repository, services, and views: Tasks 2 through 8.
- Type consistency:
  - Workspace keys match the approved spec: `outlines`, `selected_outline_id`, `script_plan`, `episode_1_script`, `storyboard_prompts`.
  - Service return shapes match the view update calls.
  - Route names match tests and templates.

---

Plan complete and saved to `docs/superpowers/plans/2026-07-02-ai-comic-drama-generator.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
