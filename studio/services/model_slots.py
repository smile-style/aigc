import os
import time

import httpx
from django.db import transaction
from django.utils import timezone

from studio.models import ModelAssignment, ModelConfig, ProviderConfig
from studio.services.model_config import (
    ensure_default_video_models,
    normalize_provider_origin,
    provider_api_key,
)
from studio.services.secret_store import encrypt_secret


CATEGORY_TEXT = "text"
CATEGORY_IMAGE = "image"
CATEGORY_VIDEO = "video"

SLOT_SPECS = {
    CATEGORY_TEXT: {
        "label": "文本生成模型",
        "purpose_label": "大纲 · 剧本 · 角色 · 分镜 Prompt",
        "provider_name": "文本生成模型服务",
        "provider_type": ProviderConfig.TYPE_OPENAI_COMPATIBLE,
        "capability": ModelConfig.CAPABILITY_TEXT,
        "purposes": (
            ModelAssignment.PURPOSE_OUTLINE,
            ModelAssignment.PURPOSE_SCRIPT,
            ModelAssignment.PURPOSE_EPISODE_SCRIPT,
            ModelAssignment.PURPOSE_CHARACTER_PROFILE,
            ModelAssignment.PURPOSE_STORYBOARD,
        ),
        "base_env_names": ("LLM_BASE_URL",),
        "key_env_names": ("LLM_API_KEY",),
        "model_env_names": ("LLM_MODEL",),
        "default_parameters": {},
        "tone": "text",
    },
    CATEGORY_IMAGE: {
        "label": "图片生成模型",
        "purpose_label": "角色图片 · 图片类资产",
        "provider_name": "图片生成模型服务",
        "provider_type": ProviderConfig.TYPE_OPENAI_COMPATIBLE,
        "capability": ModelConfig.CAPABILITY_IMAGE,
        "purposes": (ModelAssignment.PURPOSE_CHARACTER_IMAGE,),
        "base_env_names": ("IMAGE_BASE_URL", "LLM_BASE_URL"),
        "key_env_names": ("IMAGE_API_KEY", "LLM_API_KEY"),
        "model_env_names": ("IMAGE_MODEL",),
        "default_parameters": {
            "endpoint": os.environ.get("IMAGE_API_PATH", "/chat/completions"),
            "size": os.environ.get("IMAGE_SIZE", "1024x1536"),
        },
        "tone": "image",
    },
    CATEGORY_VIDEO: {
        "label": "视频生成模型",
        "purpose_label": "分镜视频",
        "provider_name": "阿里云百炼（北京）",
        "provider_type": ProviderConfig.TYPE_DASHSCOPE,
        "capability": ModelConfig.CAPABILITY_VIDEO_REFERENCE,
        "purposes": (ModelAssignment.PURPOSE_SHOT_VIDEO,),
        "base_env_names": ("VIDEO_BASE_URL",),
        "key_env_names": ("DASHSCOPE_API_KEY",),
        "model_env_names": ("VIDEO_MODEL",),
        "default_parameters": {
            "resolution": "720P",
            "ratio": "9:16",
            "duration": 5,
            "prompt_extend": False,
            "watermark": False,
            "generate_audio": False,
        },
        "tone": "video",
    },
}

STATUS_LABELS = {
    ModelConfig.VERIFICATION_UNTESTED: "未验证",
    ModelConfig.VERIFICATION_SUCCESS: "配置正常",
    ModelConfig.VERIFICATION_PARTIAL: "连接正常",
    ModelConfig.VERIFICATION_FAILED: "验证失败",
}


def ensure_simple_model_slots():
    ensure_default_video_models()
    video_model = _slot_model(SLOT_SPECS[CATEGORY_VIDEO])
    if (
        video_model
        and video_model.model_id.lower().startswith("doubao-seedance")
        and video_model.provider.provider_type != ProviderConfig.TYPE_OPENAI_COMPATIBLE
    ):
        video_model.provider.provider_type = ProviderConfig.TYPE_OPENAI_COMPATIBLE
        video_model.provider.save(update_fields=["provider_type", "updated_at"])
    for category in (CATEGORY_TEXT, CATEGORY_IMAGE):
        spec = SLOT_SPECS[category]
        model = _slot_model(spec)
        if model is None:
            base_url = _first_env(spec["base_env_names"])
            model_id = _first_env(spec["model_env_names"])
            if not base_url or not model_id:
                continue
            provider, _ = ProviderConfig.objects.get_or_create(
                name=spec["provider_name"],
                defaults={
                    "provider_type": spec["provider_type"],
                    "base_url": base_url.rstrip("/"),
                    "api_key_env_var": _configured_env_name(spec["key_env_names"]),
                    "timeout_seconds": 600,
                    "max_retries": 3,
                    "max_concurrency": 2,
                },
            )
            model, _ = ModelConfig.objects.get_or_create(
                provider=provider,
                model_id=model_id,
                capability=spec["capability"],
                defaults={
                    "name": spec["label"],
                    "default_parameters": dict(spec["default_parameters"]),
                },
            )
        for purpose in spec["purposes"]:
            ModelAssignment.objects.update_or_create(purpose=purpose, defaults={"model": model})


def get_simple_model_slots():
    ensure_simple_model_slots()
    rows = []
    for category, spec in SLOT_SPECS.items():
        model = _slot_model(spec)
        provider = model.provider if model else None
        token_tail = ""
        token_configured = False
        if provider:
            try:
                token = provider_api_key(provider)
            except Exception:
                token = ""
            token_configured = bool(token)
            token_tail = token[-4:] if token else ""
        rows.append(
            {
                "category": category,
                "label": spec["label"],
                "purpose_label": spec["purpose_label"],
                "tone": spec["tone"],
                "base_url": provider.base_url if provider else _first_env(spec["base_env_names"]),
                "model_id": model.model_id if model else _first_env(spec["model_env_names"]),
                "provider_type": provider.provider_type if provider else spec["provider_type"],
                "provider_type_choices": (
                    (
                        (ProviderConfig.TYPE_OPENAI_COMPATIBLE, "OpenAI / New API 视频"),
                        (ProviderConfig.TYPE_DASHSCOPE, "阿里百炼 / DashScope"),
                    )
                    if category == CATEGORY_VIDEO
                    else ()
                ),
                "token_configured": token_configured,
                "token_placeholder": (
                    f"已配置 ····{token_tail}" if token_tail else "输入 Token"
                ),
                "verification_status": (
                    model.verification_status
                    if model
                    else ModelConfig.VERIFICATION_UNTESTED
                ),
                "verification_label": STATUS_LABELS[
                    model.verification_status
                    if model
                    else ModelConfig.VERIFICATION_UNTESTED
                ],
                "verification_message": model.verification_message if model else "",
                "verification_latency_ms": model.verification_latency_ms if model else None,
                "last_verified_at": model.last_verified_at if model else None,
            }
        )
    return rows


@transaction.atomic
def save_simple_model_slot(category, post):
    spec = _spec(category)
    base_url = str(post.get("base_url") or "").strip().rstrip("/")
    model_id = str(post.get("model_id") or "").strip()
    token = str(post.get("api_token") or "").strip()
    provider_type = spec["provider_type"]
    if category == CATEGORY_VIDEO:
        requested_type = str(post.get("provider_type") or "").strip()
        if requested_type in {
            ProviderConfig.TYPE_OPENAI_COMPATIBLE,
            ProviderConfig.TYPE_DASHSCOPE,
        }:
            provider_type = requested_type
        elif model_id.lower().startswith("doubao-seedance"):
            provider_type = ProviderConfig.TYPE_OPENAI_COMPATIBLE
        if provider_type == ProviderConfig.TYPE_DASHSCOPE:
            base_url = normalize_provider_origin(base_url)
    if not base_url or not model_id:
        raise ValueError("Base URL 和模型 ID 不能为空。")

    current_model = _slot_model(spec)
    provider = current_model.provider if current_model else None
    if provider is None:
        provider, _ = ProviderConfig.objects.get_or_create(
            name=spec["provider_name"],
            defaults={
                "provider_type": spec["provider_type"],
                "base_url": base_url,
                "api_key_env_var": _configured_env_name(spec["key_env_names"]),
            },
        )

    configuration_changed = (
        provider.base_url.rstrip("/") != base_url
        or provider.provider_type != provider_type
        or not current_model
        or current_model.model_id != model_id
        or bool(token)
    )
    provider.provider_type = provider_type
    provider.base_url = base_url
    provider.api_key_env_var = provider.api_key_env_var or _configured_env_name(
        spec["key_env_names"]
    )
    provider.enabled = True
    provider.timeout_seconds = 600
    provider.max_retries = 3
    provider.max_concurrency = 2
    if token:
        provider.api_key_ciphertext = encrypt_secret(token)
    provider.full_clean()
    provider.save()

    defaults = (
        dict(current_model.default_parameters or {})
        if current_model
        else dict(spec["default_parameters"])
    )
    defaults.update(spec["default_parameters"])
    model, _ = ModelConfig.objects.get_or_create(
        provider=provider,
        model_id=model_id,
        capability=spec["capability"],
        defaults={"name": spec["label"], "default_parameters": defaults},
    )
    model.name = spec["label"]
    model.default_parameters = defaults
    model.enabled = True
    if configuration_changed:
        model.verification_status = ModelConfig.VERIFICATION_UNTESTED
        model.verification_message = ""
        model.verification_latency_ms = None
        model.last_verified_at = None
    model.full_clean()
    model.save()
    for purpose in spec["purposes"]:
        ModelAssignment.objects.update_or_create(purpose=purpose, defaults={"model": model})
    return model


def verify_simple_model_slot(category, client=None):
    spec = _spec(category)
    model = _slot_model(spec)
    if model is None:
        raise ValueError("请先保存模型配置。")
    provider = model.provider
    started_at = time.perf_counter()
    owns_client = client is None
    client = client or httpx.Client(trust_env=False)
    try:
        api_key = provider_api_key(provider)
        if category == CATEGORY_TEXT:
            status, message = _verify_text(client, provider, model, api_key)
        elif category == CATEGORY_IMAGE:
            status, message = _verify_image(client, provider, model, api_key)
        else:
            status, message = _verify_video(client, provider, model, api_key)
    except Exception as exc:
        status = ModelConfig.VERIFICATION_FAILED
        message = _friendly_verification_error(exc)
    finally:
        if owns_client:
            client.close()

    elapsed_ms = max(1, round((time.perf_counter() - started_at) * 1000))
    model.verification_status = status
    model.verification_message = message[:500]
    model.verification_latency_ms = elapsed_ms
    model.last_verified_at = timezone.now()
    model.save(
        update_fields=[
            "verification_status",
            "verification_message",
            "verification_latency_ms",
            "last_verified_at",
            "updated_at",
        ]
    )
    return model


def _verify_text(client, provider, model, api_key):
    response = client.post(
        f"{provider.base_url.rstrip('/')}/chat/completions",
        headers=_headers(api_key),
        json={
            "model": model.model_id,
            "messages": [{"role": "user", "content": "只回复 OK"}],
            "stream": False,
        },
        timeout=30,
    )
    _raise_for_configuration(response)
    return ModelConfig.VERIFICATION_SUCCESS, "地址、Token 与模型调用均正常。"


def _verify_image(client, provider, model, api_key):
    response = client.get(
        f"{provider.base_url.rstrip('/')}/models",
        headers=_headers(api_key),
        timeout=30,
    )
    if response.status_code in {404, 405}:
        response = client.post(
            f"{provider.base_url.rstrip('/')}/chat/completions",
            headers=_headers(api_key),
            json={
                "model": model.model_id,
                "messages": [{"role": "user", "content": "只回复 OK，不生成图片"}],
                "stream": False,
            },
            timeout=30,
        )
        _raise_for_configuration(response)
        return ModelConfig.VERIFICATION_SUCCESS, "地址、Token 与模型调用均正常。"
    _raise_for_configuration(response)
    payload = response.json()
    model_ids = {
        str(item.get("id"))
        for item in payload.get("data", [])
        if isinstance(item, dict) and item.get("id")
    }
    if model_ids and model.model_id not in model_ids:
        raise ValueError(f"模型 {model.model_id} 不在当前 Token 的可用列表中。")
    return ModelConfig.VERIFICATION_SUCCESS, "地址、Token 与模型权限均正常。"


def _verify_video(client, provider, model, api_key):
    if (
        provider.provider_type == ProviderConfig.TYPE_OPENAI_COMPATIBLE
        or model.model_id.lower().startswith("doubao-seedance")
    ):
        response = client.get(
            f"{provider.base_url.rstrip('/')}/models",
            headers=_headers(api_key),
            timeout=30,
        )
        _raise_for_configuration(response)
        payload = response.json()
        model_ids = {
            str(item.get("id"))
            for item in payload.get("data", [])
            if isinstance(item, dict) and item.get("id")
        }
        if model_ids and model.model_id not in model_ids:
            raise ValueError(f"模型 {model.model_id} 不在当前 Token 的可用列表中。")
        return ModelConfig.VERIFICATION_SUCCESS, "地址、Token 与视频模型权限均正常。"
    response = client.get(
        f"{normalize_provider_origin(provider.base_url)}/api/v1/tasks/config-validation-check",
        headers=_headers(api_key),
        timeout=30,
    )
    if response.status_code in {401, 403}:
        _raise_for_configuration(response)
    if response.status_code >= 500:
        _raise_for_configuration(response)
    return (
        ModelConfig.VERIFICATION_PARTIAL,
        "地址和 Token 正常，模型 ID 将在首次生成时最终确认。",
    )


def _headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _raise_for_configuration(response):
    if response.status_code < 400:
        return
    message = _response_message(response)
    if response.status_code in {401, 403}:
        raise ValueError("Token 无效、已过期或没有访问权限。")
    if response.status_code == 404:
        raise ValueError(f"接口或模型不存在：{message}")
    raise ValueError(f"模型验证失败（HTTP {response.status_code}）：{message}")


def _response_message(response):
    try:
        payload = response.json()
    except ValueError:
        return response.text[:240] or "服务未返回错误详情"
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "未知错误")
    return str(payload.get("message") or payload.get("code") or "未知错误")


def _friendly_verification_error(exc):
    message = str(exc)
    if "WinError 10013" in message:
        return "网络连接被 Windows 拒绝，请检查服务进程权限、防火墙或代理。"
    if isinstance(exc, httpx.TimeoutException):
        return "连接超时，请检查 Base URL 或网络状态。"
    if isinstance(exc, httpx.ConnectError):
        return f"无法连接 Base URL：{message}"
    if isinstance(exc, httpx.HTTPError):
        return f"网络请求失败：{message}"
    return message or exc.__class__.__name__


def _slot_model(spec):
    assignment = (
        ModelAssignment.objects.select_related("model__provider")
        .filter(purpose=spec["purposes"][0])
        .first()
    )
    return assignment.model if assignment else None


def _spec(category):
    try:
        return SLOT_SPECS[category]
    except KeyError as exc:
        raise ValueError("未知的模型配置类型。") from exc


def _first_env(names):
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _configured_env_name(names):
    for name in names:
        if os.environ.get(name, "").strip():
            return name
    return names[0]
