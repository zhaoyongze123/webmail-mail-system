"""对称加解密工具。

当前主要用于保护会话里暂存的邮箱密码，避免把明文直接写入 Redis。
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet

from app.config import get_settings


def _fernet() -> Fernet:
    """基于应用密钥派生一个稳定的 Fernet 实例。"""
    digest = hashlib.sha256(get_settings().app_secret_key.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_text(value: str) -> str:
    """把明文加密成可持久化存储的字符串令牌。"""
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_text(token: str) -> str:
    """解密 ``encrypt_text`` 生成的令牌。"""
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
