import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def encrypt_secret(value):
    value = str(value or "").strip()
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value):
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise ValueError("已保存的 Token 无法解密，请重新输入并保存。") from exc


def _fernet():
    source = os.environ.get("MODEL_CONFIG_ENCRYPTION_KEY", "").strip()
    if not source:
        source = f"{settings.SECRET_KEY}:aigc-model-config"
    key = base64.urlsafe_b64encode(hashlib.sha256(source.encode("utf-8")).digest())
    return Fernet(key)
