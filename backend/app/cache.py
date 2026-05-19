"""Redis 缓存、会话和用户偏好存储封装。

这个模块把 Redis 访问封装成几个小对象，用于会话管理、登录失败限流、
用户偏好读取/更新、JSON 缓存和分布式锁。
"""

from __future__ import annotations

import json
import secrets
from contextlib import contextmanager
from typing import Any, Iterator

import redis

from app.config import Settings, get_settings
from app.schemas import (
    DEFAULT_SETTINGS_LANGUAGE,
    DEFAULT_SETTINGS_MARK_READ_ON_OPEN,
    DEFAULT_SETTINGS_PAGE_SIZE,
    DEFAULT_SETTINGS_REPLY_QUOTE_POSITION,
    DEFAULT_SETTINGS_TIMEZONE,
)
from app.redis_client import get_redis_client


class SessionStore:
    """邮箱登录会话的 Redis 存储封装。"""

    def __init__(
        self,
        client: redis.Redis | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.client = client or get_redis_client()
        self.settings = settings or get_settings()

    def create(self, payload: dict[str, Any], session_id: str | None = None) -> str:
        """创建会话并写入过期时间。"""
        session_id = session_id or secrets.token_urlsafe(32)
        key = self.key(session_id)
        self.client.hset(key, mapping={field: self._encode(value) for field, value in payload.items()})
        self.client.expire(key, self.settings.session_ttl_seconds)
        return session_id

    def get(self, session_id: str) -> dict[str, Any] | None:
        """读取并反序列化会话数据。"""
        data = self.client.hgetall(self.key(session_id))
        if not data:
            return None
        return {field: self._decode(value) for field, value in data.items()}

    def refresh(self, session_id: str) -> bool:
        """刷新会话 TTL。"""
        return bool(self.client.expire(self.key(session_id), self.settings.session_ttl_seconds))

    def update(self, session_id: str, payload: dict[str, Any]) -> None:
        """更新会话字段并同步过期时间。"""
        key = self.key(session_id)
        if not payload:
            return
        self.client.hset(key, mapping={field: self._encode(value) for field, value in payload.items()})
        self.client.expire(key, self.settings.session_ttl_seconds)

    def delete(self, session_id: str) -> bool:
        """删除会话。"""
        return bool(self.client.delete(self.key(session_id)))

    @staticmethod
    def key(session_id: str) -> str:
        return f"session:{session_id}"

    @staticmethod
    def _encode(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _decode(value: str) -> Any:
        return json.loads(value)


class LoginFailureLimiter:
    """按 IP + 邮箱维度记录登录失败次数。"""

    def __init__(
        self,
        client: redis.Redis | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.client = client or get_redis_client()
        self.settings = settings or get_settings()

    def record_failure(self, ip: str, email: str) -> int:
        """记录一次失败并返回累计次数。"""
        key = self.key(ip, email)
        failures = int(self.client.incr(key))
        if failures == 1:
            self.client.expire(key, self.settings.login_fail_ttl_seconds)
        return failures

    def clear(self, ip: str, email: str) -> None:
        """清除失败计数。"""
        self.client.delete(self.key(ip, email))

    def is_limited(self, ip: str, email: str) -> bool:
        """判断指定账户是否已被限流。"""
        value = self.client.get(self.key(ip, email))
        return int(value or 0) >= self.settings.login_fail_limit

    @staticmethod
    def key(ip: str, email: str) -> str:
        normalized = email.strip().lower()
        return f"login_fail:{ip}:{normalized}"


class UserPreferenceStore:
    """邮箱用户偏好在 Redis 中的读写封装。"""

    def __init__(
        self,
        client: redis.Redis | None = None,
    ) -> None:
        self.client = client or get_redis_client()

    def get(self, email: str) -> dict[str, Any]:
        """读取用户偏好，缺省时返回系统默认值。"""
        data = self.client.hgetall(self.key(email))
        preferences: dict[str, Any] = {
            "page_size": DEFAULT_SETTINGS_PAGE_SIZE,
            "mark_read_on_open": DEFAULT_SETTINGS_MARK_READ_ON_OPEN,
            "reply_quote_position": DEFAULT_SETTINGS_REPLY_QUOTE_POSITION,
            "language": DEFAULT_SETTINGS_LANGUAGE,
            "timezone": DEFAULT_SETTINGS_TIMEZONE,
        }
        if data:
            for field, value in data.items():
                preferences[field] = self._decode(value)
        return preferences

    def update(self, email: str, payload: dict[str, Any]) -> dict[str, Any]:
        """合并更新用户偏好。"""
        current = self.get(email)
        for field, value in payload.items():
            if value is not None:
                current[field] = value
        self.client.hset(self.key(email), mapping={field: self._encode(value) for field, value in current.items()})
        return current

    @staticmethod
    def key(email: str) -> str:
        normalized = email.strip().lower()
        return f"user_preferences:{normalized}"

    @staticmethod
    def _encode(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _decode(value: str) -> Any:
        return json.loads(value)


class JsonCache:
    """带 JSON 序列化的轻量缓存封装。"""

    def __init__(self, client: redis.Redis | None = None) -> None:
        self.client = client or get_redis_client()

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """写入 JSON 缓存。"""
        try:
            self.client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl_seconds)
        except redis.RedisError:
            return None

    def get(self, key: str) -> Any | None:
        """读取 JSON 缓存。"""
        try:
            value = self.client.get(key)
        except redis.RedisError:
            return None
        if value is None:
            return None
        return json.loads(value)

    def delete(self, key: str) -> bool:
        """删除缓存键。"""
        try:
            return bool(self.client.delete(key))
        except redis.RedisError:
            return False


class RedisLock:
    """基于 Redis SET NX 的简易分布式锁。"""

    def __init__(self, client: redis.Redis | None = None) -> None:
        self.client = client or get_redis_client()

    @contextmanager
    def acquire(self, key: str, ttl_seconds: int, token: str | None = None) -> Iterator[bool]:
        """获取锁并在退出时尽量释放。"""
        token = token or secrets.token_urlsafe(16)
        acquired = bool(self.client.set(key, token, nx=True, ex=ttl_seconds))
        try:
            yield acquired
        finally:
            if acquired and self.client.get(key) == token:
                self.client.delete(key)
