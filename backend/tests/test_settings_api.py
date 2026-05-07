from __future__ import annotations

import importlib
import sys
from email.message import EmailMessage
from email.utils import format_datetime
from types import ModuleType
from datetime import UTC, datetime

import fakeredis
import pydantic.networks as pydantic_networks
import pytest
from fastapi.testclient import TestClient

from app.mail_adapters import MailAdapterError


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

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


class FakeImapAdapter:
    login_calls: list[tuple[str, str]] = []
    mark_seen_calls: list[str] = []
    last_instance: "FakeImapAdapter | None" = None

    def __init__(self, settings) -> None:
        self.settings = settings
        self.selected_folder: str | None = None
        self.connected = False
        self.logged_in = False
        self.logged_out = False
        FakeImapAdapter.last_instance = self

    @classmethod
    def reset(cls) -> None:
        cls.login_calls = []
        cls.mark_seen_calls = []
        cls.last_instance = None

    def connect(self):
        self.connected = True
        return self

    def login(self):
        FakeImapAdapter.login_calls.append((self.settings.username, self.settings.password))
        if getattr(self.settings, "password", None) == "wrong-password":
            raise MailAdapterError("IMAP 登录失败", operation="login")
        self.logged_in = True
        return self

    def logout(self):
        self.logged_out = True
        return self

    def select_folder(self, folder: str):
        self.selected_folder = folder
        return "OK", [b"1"]

    def uid_search(self, criteria: str):
        if self.selected_folder == "INBOX":
            return ["101"]
        return []

    def uid_fetch_message_bytes(self, uid: str | bytes) -> bytes:
        uid_text = uid.decode("utf-8") if isinstance(uid, bytes) else str(uid)
        if self.selected_folder == "INBOX" and uid_text == "101":
            message = EmailMessage()
            message["Subject"] = "设置测试邮件"
            message["From"] = "Tester <tester@example.com>"
            message["To"] = "reader@example.com"
            message["Date"] = format_datetime(datetime(2026, 5, 7, 9, 0, tzinfo=UTC))
            message.set_content("这是一封用于设置测试的邮件正文。")
            return message.as_bytes()
        raise MailAdapterError("IMAP 邮件不存在", operation="fetch_message_bytes")

    def fetch_message_bytes(self, uid: str | bytes) -> bytes:
        return self.uid_fetch_message_bytes(uid)

    def mark_seen(self, uid: str | bytes):
        uid_text = uid.decode("utf-8") if isinstance(uid, bytes) else str(uid)
        FakeImapAdapter.mark_seen_calls.append(uid_text)
        return self


def build_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    FakeImapAdapter.reset()
    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    settings = FakeSettings()

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
    mail_adapters_module = importlib.import_module("app.mail_adapters")

    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_module, "get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(redis_client_module, "get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(mail_adapters_module, "ImapAdapter", FakeImapAdapter)

    sys.modules.pop("app.auth", None)
    sys.modules.pop("app.mailbox", None)
    sys.modules.pop("app.main", None)
    auth_module = importlib.import_module("app.auth")
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings, raising=False)
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: fake_redis, raising=False)

    main_module = importlib.import_module("app.main")
    monkeypatch.setattr(main_module, "get_settings", lambda: settings, raising=False)
    return TestClient(main_module.app, raise_server_exceptions=False)


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


def test_settings_requires_login(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)

    response = client.get("/api/settings")

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_settings_returns_current_account_and_default_preferences(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)

    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    response = client.get("/api/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["account"]["email"] == "user@example.com"
    assert body["data"]["preferences"]["page_size"] == 30
    assert body["data"]["preferences"]["mark_read_on_open"] is True


def test_settings_update_persists_and_affects_mail_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)

    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    update_response = client.put(
        "/api/settings",
        json={
            "page_size": 12,
            "mark_read_on_open": False,
        },
    )

    assert update_response.status_code == 200
    update_body = update_response.json()
    assert update_body["data"]["preferences"]["page_size"] == 12
    assert update_body["data"]["preferences"]["mark_read_on_open"] is False

    refresh_response = client.get("/api/settings")
    assert refresh_response.status_code == 200
    refresh_body = refresh_response.json()
    assert refresh_body["data"]["preferences"]["page_size"] == 12
    assert refresh_body["data"]["preferences"]["mark_read_on_open"] is False

    list_response = client.get("/api/folders/INBOX/messages")
    assert list_response.status_code == 200
    list_body = list_response.json()
    assert list_body["data"]["page_size"] == 12

    detail_response = client.get("/api/folders/INBOX/messages/101")
    assert detail_response.status_code == 200
    assert FakeImapAdapter.mark_seen_calls == []


def test_logout_invalidates_current_session(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)

    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 200
    assert logout_response.json()["success"] is True

    settings_response = client.get("/api/settings")
    assert settings_response.status_code == 401
    body = settings_response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "AUTH_SESSION_EXPIRED"
