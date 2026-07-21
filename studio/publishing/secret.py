import base64
import hashlib
import json
import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

from .errors import PublishingAuthError


def encrypt_credentials(value):
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True)
    return _fernet().encrypt(payload.encode("utf-8")).decode("ascii")


def decrypt_credentials(value):
    try:
        payload = _fernet().decrypt(str(value or "").encode("ascii")).decode("utf-8")
        result = json.loads(payload)
    except (InvalidToken, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublishingAuthError("平台登录凭据无法解密，请重新登录。") from exc
    if not isinstance(result, dict):
        raise PublishingAuthError("平台登录凭据格式无效，请重新登录。")
    return {str(key): str(item) for key, item in result.items()}


def encrypt_login_key(value):
    return _fernet().encrypt(str(value).encode("utf-8")).decode("ascii")


def decrypt_login_key(value):
    try:
        return _fernet().decrypt(str(value).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise PublishingAuthError("登录会话凭据无法解密，请重新扫码。") from exc


def _fernet():
    source = os.environ.get("PUBLISHING_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not source:
        source = f"{settings.SECRET_KEY}:aigc-publishing"
    key = base64.urlsafe_b64encode(hashlib.sha256(source.encode("utf-8")).digest())
    return Fernet(key)
