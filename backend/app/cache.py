from __future__ import annotations

import json
import secrets
from contextlib import contextmanager
from typing import Any, Iterator

import redis

from app.config import Settings, get_settings
from app.redis_client import get_redis_client

DEFAULT_PAGE_SIZE = 30
DEFAULT_MARK_READ_ON_OPEN = True


class SessionStore:
    def __init__(
        self,
        client: redis.Redis | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.client = client or get_redis_client()
        self.settings = settings or get_settings()

    def create(self, payload: dict[str, Any], session_id: str | None = None) -> str:
        session_id = session_id or secrets.token_urlsafe(32)
        key = self.key(session_id)
        self.client.hset(key, mapping={field: self._encode(value) for field, value in payload.items()})
        self.client.expire(key, self.settings.session_ttl_seconds)
        return session_id

    def get(self, session_id: str) -> dict[str, Any] | None:
        data = self.client.hgetall(self.key(session_id))
        if not data:
            return None
        return {field: self._decode(value) for field, value in data.items()}

    def refresh(self, session_id: str) -> bool:
        return bool(self.client.expire(self.key(session_id), self.settings.session_ttl_seconds))

    def delete(self, session_id: str) -> bool:
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
    def __init__(
        self,
        client: redis.Redis | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.client = client or get_redis_client()
        self.settings = settings or get_settings()

    def record_failure(self, ip: str, email: str) -> int:
        key = self.key(ip, email)
        failures = int(self.client.incr(key))
        if failures == 1:
            self.client.expire(key, self.settings.login_fail_ttl_seconds)
        return failures

    def clear(self, ip: str, email: str) -> None:
        self.client.delete(self.key(ip, email))

    def is_limited(self, ip: str, email: str) -> bool:
        value = self.client.get(self.key(ip, email))
        return int(value or 0) >= self.settings.login_fail_limit

    @staticmethod
    def key(ip: str, email: str) -> str:
        normalized = email.strip().lower()
        return f"login_fail:{ip}:{normalized}"


class UserPreferenceStore:
    def __init__(
        self,
        client: redis.Redis | None = None,
    ) -> None:
        self.client = client or get_redis_client()

    def get(self, email: str) -> dict[str, Any]:
        data = self.client.hgetall(self.key(email))
        preferences: dict[str, Any] = {
            "page_size": DEFAULT_PAGE_SIZE,
            "mark_read_on_open": DEFAULT_MARK_READ_ON_OPEN,
        }
        if data:
            for field in preferences:
                if field in data:
                    preferences[field] = self._decode(data[field])
        return preferences

    def update(self, email: str, payload: dict[str, Any]) -> dict[str, Any]:
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
    def __init__(self, client: redis.Redis | None = None) -> None:
        self.client = client or get_redis_client()

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        try:
            self.client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl_seconds)
        except redis.RedisError:
            return None

    def get(self, key: str) -> Any | None:
        try:
            value = self.client.get(key)
        except redis.RedisError:
            return None
        if value is None:
            return None
        return json.loads(value)

    def delete(self, key: str) -> bool:
        try:
            return bool(self.client.delete(key))
        except redis.RedisError:
            return False


class RedisLock:
    def __init__(self, client: redis.Redis | None = None) -> None:
        self.client = client or get_redis_client()

    @contextmanager
    def acquire(self, key: str, ttl_seconds: int, token: str | None = None) -> Iterator[bool]:
        token = token or secrets.token_urlsafe(16)
        acquired = bool(self.client.set(key, token, nx=True, ex=ttl_seconds))
        try:
            yield acquired
        finally:
            if acquired and self.client.get(key) == token:
                self.client.delete(key)
