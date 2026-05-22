from __future__ import annotations

import base64
import importlib
import json
import sys
from datetime import datetime, timezone
from types import ModuleType

import fakeredis
import pydantic.networks as pydantic_networks
import pytest
from fastapi.testclient import TestClient

from app.mail_adapters import MailAdapterError


UTC = timezone.utc


class FakeSettings:
    def __init__(self, *, login_fail_limit: int = 5) -> None:
        self.app_env = "test"
        self.app_name = "webmail-mvp"
        self.app_secret_key = "test-secret"
        self.cors_origins = "http://localhost:5173,http://127.0.0.1:5173"
        self.session_ttl_seconds = 60
        self.session_cookie_name = "webmail_session"
        self.session_cookie_secure = False
        self.login_fail_ttl_seconds = 30
        self.login_fail_limit = login_fail_limit
        self.mail_imap_host = "imap.test.local"
        self.mail_imap_port = 143
        self.mail_imap_ssl = False
        self.mail_imap_starttls = False
        self.mail_smtp_host = "smtp.test.local"
        self.mail_smtp_port = 25
        self.mail_smtp_ssl = False
        self.mail_smtp_starttls = False
        self.attachment_max_size_bytes = 9 * 1024 * 1024
        self.attachment_upload_max_size_bytes = 9 * 1024 * 1024
        self.max_attachment_upload_size_bytes = 9 * 1024 * 1024
        self.attachments_max_size_bytes = 9 * 1024 * 1024
        self.attachment_max_total_size_bytes = 9 * 1024 * 1024
        self.attachment_upload_ttl_seconds = 3600
        self.attachment_ttl_seconds = 3600
        self.attachment_temp_ttl_seconds = 3600
        self.attachments_ttl_seconds = 3600

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


class FakeImapAdapter:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.connected = False
        self.logged_in = False
        self.logged_out = False

    def connect(self):
        self.connected = True
        return self

    def login(self):
        if self.settings.password == "wrong-password":
            raise MailAdapterError("IMAP 登录失败", operation="login")
        self.logged_in = True
        return self

    def logout(self):
        self.logged_out = True
        return self


def make_settings(*, login_fail_limit: int = 5) -> FakeSettings:
    return FakeSettings(login_fail_limit=login_fail_limit)


def _purge_app_modules() -> None:
    for module_name in list(sys.modules):
        if module_name.startswith("app.") and (
            module_name == "app.main" or "attachment" in module_name
        ):
            sys.modules.pop(module_name, None)


def build_client(monkeypatch: pytest.MonkeyPatch, *, login_fail_limit: int = 5) -> tuple[TestClient, fakeredis.FakeRedis]:
    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    settings = make_settings(login_fail_limit=login_fail_limit)

    crypto_module = ModuleType("app.crypto")
    crypto_module.encrypt_text = lambda value: value
    crypto_module.decrypt_text = lambda token: token
    monkeypatch.setitem(sys.modules, "app.crypto", crypto_module)

    email_validator_module = ModuleType("email_validator")

    class EmailNotValidError(ValueError):
        pass

    class _ValidatedEmail:
        def __init__(self, email: str) -> None:
            self.normalized = email.strip().lower()
            self.local_part = self.normalized.split("@", 1)[0]

    def validate_email(value: str, check_deliverability: bool = False):
        return _ValidatedEmail(value)

    email_validator_module.EmailNotValidError = EmailNotValidError
    email_validator_module.validate_email = validate_email
    monkeypatch.setitem(sys.modules, "email_validator", email_validator_module)

    original_version = pydantic_networks.version
    monkeypatch.setattr(
        pydantic_networks,
        "version",
        lambda package_name: "2.0.0" if package_name == "email-validator" else original_version(package_name),
    )

    config_module = importlib.import_module("app.config")
    cache_module = importlib.import_module("app.cache")
    redis_client_module = importlib.import_module("app.redis_client")

    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_module, "get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(redis_client_module, "get_redis_client", lambda: fake_redis)

    _purge_app_modules()
    auth_module = importlib.import_module("app.auth")
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings, raising=False)
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: fake_redis, raising=False)
    monkeypatch.setattr(auth_module, "ImapAdapter", FakeImapAdapter)

    _purge_app_modules()
    main_module = importlib.import_module("app.main")
    return TestClient(main_module.app, raise_server_exceptions=False), fake_redis


def login(client: TestClient, email: str, password: str, *, remember: bool = False):
    response = client.post(
        "/api/auth/login",
        json={
            "email": email,
            "password": password,
            "remember": remember,
        },
    )
    csrf_token = client.cookies.get("webmail_csrf")
    if response.status_code == 200 and csrf_token:
        client.headers.update({"X-CSRF-Token": csrf_token})
    return response


def _make_file(name: str, content_type: str, content: bytes) -> tuple[str, tuple[str, bytes, str]]:
    return ("files", (name, content, content_type))


def _make_chunk_file(name: str, content_type: str, content: bytes) -> tuple[str, tuple[str, bytes, str]]:
    return ("chunk", (name, content, content_type))


def _extract_attachment_items(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("attachments", "items", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    pytest.fail(f"无法从响应中解析附件列表：{payload!r}")


def _extract_error(body: dict[str, object]) -> dict[str, object]:
    error = body.get("error")
    assert isinstance(error, dict)
    return error


def _find_redis_key(fake_redis: fakeredis.FakeRedis, attachment_id: str) -> str:
    for key in fake_redis.keys("*"):
        key_text = key if isinstance(key, str) else key.decode()
        if attachment_id in key_text:
            return key_text
        try:
            stored_value = fake_redis.get(key_text)
        except Exception:
            stored_value = None
        if isinstance(stored_value, str) and attachment_id in stored_value:
            return key_text
        try:
            stored_hash = fake_redis.hgetall(key_text)
        except Exception:
            stored_hash = {}
        if stored_hash and attachment_id in json.dumps(stored_hash, ensure_ascii=False):
            return key_text
    pytest.fail(f"未找到包含附件 ID {attachment_id} 的 Redis 键")


def _upload_chunk(
    client: TestClient,
    *,
    attachment_id: str,
    filename: str,
    content_type: str,
    content: bytes,
    chunk_index: int,
    total_chunks: int,
    file_size_bytes: int,
) -> object:
    response = client.post(
        "/api/attachments/chunks",
        data={
            "attachment_id": attachment_id,
            "chunk_index": str(chunk_index),
            "total_chunks": str(total_chunks),
            "file_size_bytes": str(file_size_bytes),
            "filename": filename,
            "content_type": content_type,
        },
        files=[_make_chunk_file(filename, content_type, content)],
    )
    return response


def _parse_datetime(value: object) -> datetime:
    assert isinstance(value, str)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    assert parsed.tzinfo is not None
    return parsed


def test_upload_attachment_requires_login(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = build_client(monkeypatch)

    response = client.post(
        "/api/attachments",
        files=[_make_file("draft.txt", "text/plain", b"draft")],
    )

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert _extract_error(body)["code"] == "AUTH_SESSION_EXPIRED"


def test_upload_multiple_attachments_saves_temp_data_in_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    client, fake_redis = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    response = client.post(
        "/api/attachments",
        files=[
            _make_file("report.pdf", "application/pdf", b"%PDF-1.4 fake pdf"),
            _make_file("../evil.txt", "text/plain", b"evil"),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    attachments = _extract_attachment_items(body["data"])
    assert len(attachments) == 2

    first_attachment = attachments[0]
    assert first_attachment["attachment_id"]
    assert first_attachment["filename"] == "report.pdf"
    assert first_attachment["content_type"] == "application/pdf"
    assert first_attachment["size_bytes"] == len(b"%PDF-1.4 fake pdf")
    assert "expires_at" in first_attachment
    parsed_expires_at = _parse_datetime(first_attachment["expires_at"])
    assert parsed_expires_at > datetime.now(tz=UTC)

    second_attachment = attachments[1]
    assert second_attachment["attachment_id"]
    assert second_attachment["filename"] != "../evil.txt"
    assert "/" not in second_attachment["filename"]
    assert "\\" not in second_attachment["filename"]
    assert ".." not in second_attachment["filename"]
    assert second_attachment["filename"].endswith("evil.txt")
    assert second_attachment["content_type"] == "text/plain"
    assert second_attachment["size_bytes"] == len(b"evil")
    assert "expires_at" in second_attachment
    _parse_datetime(second_attachment["expires_at"])

    attachment_id = str(first_attachment["attachment_id"])
    redis_key = _find_redis_key(fake_redis, attachment_id)
    ttl_seconds = fake_redis.ttl(redis_key)
    assert ttl_seconds > 0

    try:
        stored_value = fake_redis.get(redis_key)
    except Exception:
        stored_value = None
    if stored_value is None:
        stored_value = fake_redis.hgetall(redis_key)
    assert stored_value
    stored_text = json.dumps(stored_value, ensure_ascii=False) if isinstance(stored_value, dict) else str(stored_value)
    assert attachment_id in stored_text
    assert "report.pdf" in stored_text
    assert "%PDF-1.4 fake pdf" in stored_text


def test_upload_attachment_total_size_over_limit_returns_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    over_limit_first = b"a" * (5 * 1024 * 1024)
    over_limit_second = b"b" * (4 * 1024 * 1024 + 1)
    response = client.post(
        "/api/attachments",
        files=[
            _make_file("big-1.bin", "application/octet-stream", over_limit_first),
            _make_file("big-2.bin", "application/octet-stream", over_limit_second),
        ],
    )

    assert response.status_code == 413
    body = response.json()
    assert body["success"] is False
    error = _extract_error(body)
    assert error["code"] == "ATTACHMENT_TOO_LARGE"


def test_upload_attachment_chunk_assembles_and_persists_temp_attachment(monkeypatch: pytest.MonkeyPatch) -> None:
    client, fake_redis = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    attachment_id = "chunk-test-001"
    payload = b"chunked-attachment-payload"
    first_half = payload[:10]
    second_half = payload[10:]

    first_response = _upload_chunk(
        client,
        attachment_id=attachment_id,
        filename="large.bin",
        content_type="application/octet-stream",
        content=first_half,
        chunk_index=0,
        total_chunks=2,
        file_size_bytes=len(payload),
    )
    assert first_response.status_code == 200
    first_body = first_response.json()
    assert first_body["success"] is True
    first_attachment = first_body["data"]["attachment"]
    assert first_attachment["complete"] is False
    assert first_attachment["uploaded_chunks"] == 1
    assert first_attachment["total_chunks"] == 2

    second_response = _upload_chunk(
        client,
        attachment_id=attachment_id,
        filename="large.bin",
        content_type="application/octet-stream",
        content=second_half,
        chunk_index=1,
        total_chunks=2,
        file_size_bytes=len(payload),
    )
    assert second_response.status_code == 200
    second_body = second_response.json()
    assert second_body["success"] is True
    second_attachment = second_body["data"]["attachment"]
    assert second_attachment["complete"] is True
    assert second_attachment["uploaded_chunks"] == 2
    assert second_attachment["total_chunks"] == 2
    assert second_attachment["size_bytes"] == len(payload)

    temp_key = f"attachment:temp:user@example.com:{attachment_id}"
    stored = fake_redis.hgetall(temp_key)
    assert stored
    assert base64.b64decode(str(stored["content_b64"]).encode("ascii")) == payload


def test_upload_attachment_chunk_rejects_invalid_attachment_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    response = _upload_chunk(
        client,
        attachment_id="../invalid",
        filename="broken.bin",
        content_type="application/octet-stream",
        content=b"broken",
        chunk_index=0,
        total_chunks=1,
        file_size_bytes=6,
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert _extract_error(body)["code"] == "ATTACHMENT_INVALID_ID"
