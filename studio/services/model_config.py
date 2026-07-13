import json
import os

from studio.llm.image_provider import ImageConfig, ImageProvider
from studio.llm.provider import LLMConfig, LLMConfigurationError, LLMProvider
from studio.models import ModelAssignment, ModelConfig, ProviderConfig


DEFAULT_BAILIAN_ORIGIN = (
    "https://ws-vdsi8p8xwd4yw9rc.cn-beijing.maas.aliyuncs.com"
)


def ensure_default_video_models():
    provider, _ = ProviderConfig.objects.get_or_create(
        name="阿里云百炼（北京）",
        defaults={
            "provider_type": ProviderConfig.TYPE_DASHSCOPE,
            "base_url": DEFAULT_BAILIAN_ORIGIN,
            "api_key_env_var": "DASHSCOPE_API_KEY",
            "timeout_seconds": 600,
            "max_retries": 3,
            "max_concurrency": 2,
        },
    )
    defaults = [
        (
            "万相 2.7 参考生视频",
            "wan2.7-r2v",
            ModelConfig.CAPABILITY_VIDEO_REFERENCE,
            {"resolution": "720P", "ratio": "9:16", "duration": 5, "prompt_extend": True, "watermark": False},
            ModelAssignment.PURPOSE_SHOT_VIDEO,
        ),
        (
            "万相 2.7 图生视频",
            "wan2.7-i2v-2026-04-25",
            ModelConfig.CAPABILITY_VIDEO_IMAGE,
            {"resolution": "720P", "duration": 5, "prompt_extend": True, "watermark": False},
            ModelAssignment.PURPOSE_SHOT_VIDEO_FALLBACK,
        ),
        (
            "万相 2.7 视频编辑",
            "wan2.7-videoedit",
            ModelConfig.CAPABILITY_VIDEO_EDIT,
            {"resolution": "720P", "prompt_extend": True, "watermark": False},
            ModelAssignment.PURPOSE_VIDEO_EDIT,
        ),
    ]
    for name, model_id, capability, parameters, purpose in defaults:
        model, _ = ModelConfig.objects.get_or_create(
            provider=provider,
            model_id=model_id,
            capability=capability,
            defaults={"name": name, "default_parameters": parameters},
        )
        ModelAssignment.objects.get_or_create(purpose=purpose, defaults={"model": model})
    return provider


def provider_api_key(provider):
    key = os.environ.get(provider.api_key_env_var, "").strip()
    if not key:
        raise LLMConfigurationError(
            f"环境变量 {provider.api_key_env_var} 尚未配置，无法调用 {provider.name}。"
        )
    return key


def assigned_model(purpose, required_capability=None):
    assignment = (
        ModelAssignment.objects.select_related("model__provider")
        .filter(purpose=purpose, model__enabled=True, model__provider__enabled=True)
        .first()
    )
    if assignment is None:
        return None
    if required_capability and assignment.model.capability != required_capability:
        raise ValueError(f"{assignment.get_purpose_display()} 的模型能力不匹配。")
    return assignment.model


def llm_provider_for(purpose):
    model = assigned_model(purpose, ModelConfig.CAPABILITY_TEXT)
    if model is None:
        return LLMProvider.from_env()
    provider = model.provider
    return LLMProvider(
        LLMConfig(
            base_url=provider.base_url.rstrip("/"),
            api_key=provider_api_key(provider),
            model=model.model_id,
        ),
        timeout=provider.timeout_seconds,
    )


def image_provider_for():
    model = assigned_model(ModelAssignment.PURPOSE_CHARACTER_IMAGE, ModelConfig.CAPABILITY_IMAGE)
    if model is None:
        return ImageProvider.from_env()
    provider = model.provider
    parameters = model.default_parameters or {}
    return ImageProvider(
        ImageConfig(
            base_url=provider.base_url.rstrip("/"),
            api_key=provider_api_key(provider),
            model=model.model_id,
            endpoint=str(parameters.get("endpoint", "/chat/completions")),
            size=str(parameters.get("size", "1024x1536")),
        )
    )


def save_provider_from_post(post):
    provider_id = post.get("provider_id")
    provider = ProviderConfig.objects.filter(pk=provider_id).first() if provider_id else ProviderConfig()
    provider.name = post.get("name", "").strip()
    provider.provider_type = post.get("provider_type", ProviderConfig.TYPE_DASHSCOPE)
    provider.base_url = normalize_provider_origin(post.get("base_url", ""))
    provider.api_key_env_var = post.get("api_key_env_var", "DASHSCOPE_API_KEY").strip()
    provider.timeout_seconds = _positive_int(post.get("timeout_seconds"), 600)
    provider.max_retries = _positive_int(post.get("max_retries"), 3, allow_zero=True)
    provider.max_concurrency = _positive_int(post.get("max_concurrency"), 2)
    provider.enabled = post.get("enabled") == "on"
    if not provider.name or not provider.base_url or not provider.api_key_env_var:
        raise ValueError("服务名称、基础地址和 API Key 环境变量不能为空。")
    provider.full_clean()
    provider.save()
    return provider


def save_model_from_post(post):
    model_id = post.get("config_id")
    model = ModelConfig.objects.filter(pk=model_id).first() if model_id else ModelConfig()
    model.provider = ProviderConfig.objects.get(pk=post.get("provider"))
    model.name = post.get("name", "").strip()
    model.model_id = post.get("model_id", "").strip()
    model.capability = post.get("capability", "")
    model.enabled = post.get("enabled") == "on"
    raw_parameters = post.get("default_parameters", "{}").strip() or "{}"
    try:
        model.default_parameters = json.loads(raw_parameters)
    except json.JSONDecodeError as exc:
        raise ValueError("默认参数必须是合法 JSON。") from exc
    if not isinstance(model.default_parameters, dict):
        raise ValueError("默认参数必须是 JSON 对象。")
    model.full_clean()
    model.save()
    return model


def normalize_provider_origin(value):
    value = value.strip().rstrip("/")
    suffix = "/compatible-mode/v1"
    if value.endswith(suffix):
        value = value[: -len(suffix)]
    return value


def _positive_int(value, default, allow_zero=False):
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    minimum = 0 if allow_zero else 1
    return result if result >= minimum else default
