from __future__ import annotations

import importlib
import logging
import sys
from types import ModuleType

import fakeredis
import pydantic.networks as pydantic_networks
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.mail_adapters import MailAdapterError


class FakeSettings:
    def __init__(self, *, session_cookie_secure: bool = False) -> None:
        self.app_env = "test"
        self.app_name = "webmail-mvp"
        self.app_secret_key = "test-secret"
        self.database_url = "sqlite+pysqlite:///:memory:"
        self.cors_origins = "http://localhost:5173,http://127.0.0.1:5173"
        self.session_ttl_seconds = 60
        self.session_cookie_name = "webmail_session"
        self.session_cookie_secure = session_cookie_secure
        self.login_fail_ttl_seconds = 30
        self.login_fail_limit = 5
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
        self.attachment_preview_cache_dir = "/tmp/webmail-preview-cache-test"
        self.attachment_preview_cache_ttl_seconds = 3600
        self.attachment_preview_cache_max_mb = 32
        self.attachment_preview_housekeeping_interval_seconds = 1
        self.attachment_preview_processing_timeout_seconds = 30

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


class FakeImapAdapter:
    def __init__(self, settings) -> None:
        self.settings = settings

    def connect(self):
        return self

    def login(self):
        if getattr(self.settings, "password", None) == "wrong-password":
            raise MailAdapterError("IMAP 登录失败", operation="login")
        return self

    def logout(self):
        return self

    def select_folder(self, folder: str):
        return "OK", [b"1"]

    def uid_search(self, criteria: str):
        return []

    def uid_fetch_message_bytes(self, uid: str | bytes) -> bytes:
        raise MailAdapterError("IMAP 邮件不存在", operation="fetch_message_bytes")

    def fetch_message_bytes(self, uid: str | bytes) -> bytes:
        return self.uid_fetch_message_bytes(uid)


def build_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    session_cookie_secure: bool = False,
    base_url: str = "http://testserver",
) -> tuple[TestClient, fakeredis.FakeRedis]:
    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    settings = FakeSettings(session_cookie_secure=session_cookie_secure)

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
    db_module = importlib.import_module("app.db")
    mail_adapters_module = importlib.import_module("app.mail_adapters")

    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_module, "get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(redis_client_module, "get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(mail_adapters_module, "ImapAdapter", FakeImapAdapter)

    sys.modules.pop("app.mail_state", None)
    sys.modules.pop("app.mail_preferences", None)
    models_module = importlib.import_module("app.models")
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(db_module, "get_engine", lambda database_url=None: engine)
    models_module.Base.metadata.create_all(engine)

    sys.modules.pop("app.auth", None)
    sys.modules.pop("app.attachments", None)
    sys.modules.pop("app.mailbox", None)
    sys.modules.pop("app.compose", None)
    sys.modules.pop("app.contacts", None)
    sys.modules.pop("app.drafts", None)
    sys.modules.pop("app.signatures", None)
    sys.modules.pop("app.main", None)

    auth_module = importlib.import_module("app.auth")
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings, raising=False)
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: fake_redis, raising=False)
    monkeypatch.setattr(auth_module, "ImapAdapter", FakeImapAdapter)

    main_module = importlib.import_module("app.main")
    return TestClient(main_module.app, raise_server_exceptions=False, base_url=base_url), fake_redis


def login(client: TestClient, email: str, password: str):
    return client.post("/api/auth/login", json={"email": email, "password": password, "remember": False})


def test_security_headers_cookie_and_csrf(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = build_client(monkeypatch)

    response = login(client, "user@example.com", "correct-password")

    assert response.status_code == 200
    headers = response.headers
    assert headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    set_cookie = headers.get("set-cookie", "")
    assert "webmail_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "webmail_csrf=" in set_cookie

    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

    csrf_response = client.put("/api/settings", json={"system": {"page_size": 12}})
    assert csrf_response.status_code == 403
    assert csrf_response.json()["error"]["code"] == "CSRF_TOKEN_INVALID"


def test_http_request_does_not_force_secure_cookie_even_if_env_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = build_client(monkeypatch, session_cookie_secure=True)

    response = login(client, "user@example.com", "correct-password")

    assert response.status_code == 200
    assert "Secure" not in response.headers.get("set-cookie", "")


def test_https_request_keeps_secure_cookie_when_env_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = build_client(
        monkeypatch,
        session_cookie_secure=True,
        base_url="https://testserver",
    )

    response = login(client, "user@example.com", "correct-password")

    assert response.status_code == 200
    assert "Secure" in response.headers.get("set-cookie", "")


def test_attachment_invalid_id_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = build_client(monkeypatch)
    response = login(client, "user@example.com", "correct-password")
    assert response.status_code == 200

    download_response = client.get("/api/folders/INBOX/messages/101/attachments/..evil")
    assert download_response.status_code == 400
    assert download_response.json()["error"]["code"] == "ATTACHMENT_INVALID_ID"


def test_attachment_preview_allows_same_origin_frame_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = build_client(monkeypatch)
    response = login(client, "user@example.com", "correct-password")
    assert response.status_code == 200

    preview_response = client.get("/api/folders/INBOX/messages/101/attachments/1/preview")

    assert preview_response.status_code in {404, 415, 502}
    assert preview_response.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in preview_response.headers["content-security-policy"]


def test_sanitized_logging_excludes_sensitive_payloads(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    client, _ = build_client(monkeypatch)
    caplog.set_level(logging.INFO, logger="app.security")

    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    from app.security import log_sanitized_event

    log_sanitized_event(
        "manual",
        password="secret",
        cookie="session=value",
        html_body="<p>邮件正文</p>",
        text_body="纯文本正文",
        content_b64="c2VjcmV0",
        nested={"token": "abc123", "attachment": {"content": "payload"}},
    )

    client.get("/api/auth/me")

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "correct-password" not in log_text
    assert "secret" not in log_text
    assert "session=value" not in log_text
    assert "邮件正文" not in log_text
    assert "纯文本正文" not in log_text
    assert "c2VjcmV0" not in log_text
    assert "payload" not in log_text
