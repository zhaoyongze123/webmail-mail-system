from __future__ import annotations

import importlib
import sys
from types import ModuleType

import fakeredis
import pydantic.networks as pydantic_networks
import pytest
from fastapi.testclient import TestClient

from app.cache import SessionStore
from app.mail_adapters import MailAdapterError


class FakeSettings:
    def __init__(self) -> None:
        self.app_env = "test"
        self.app_name = "webmail-mvp"
        self.app_secret_key = "test-secret"
        self.cors_origins = "http://localhost:5173,http://127.0.0.1:5173"
        self.session_ttl_seconds = 60
        self.session_cookie_name = "webmail_session"
        self.session_cookie_secure = False
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

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


class FakeImapAdapter:
    def __init__(self, settings) -> None:
        self.settings = settings

    def connect(self):
        return self

    def login(self):
        if self.settings.password == "wrong-password":
            raise MailAdapterError("IMAP 登录失败", operation="login")
        return self

    def logout(self):
        return self


def _purge_app_modules() -> None:
    for module_name in list(sys.modules):
        if module_name.startswith("app.") and module_name in {"app.auth", "app.main", "app.contacts"}:
            sys.modules.pop(module_name, None)


def build_client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, fakeredis.FakeRedis]:
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

    _purge_app_modules()
    auth_module = importlib.import_module("app.auth")
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings, raising=False)
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: fake_redis, raising=False)
    monkeypatch.setattr(auth_module, "ImapAdapter", FakeImapAdapter)

    _purge_app_modules()
    main_module = importlib.import_module("app.main")
    return TestClient(main_module.app, raise_server_exceptions=False), fake_redis


def login(client: TestClient, email: str, password: str):
    return client.post(
        "/api/auth/login",
        json={"email": email, "password": password, "remember": False},
    )


def test_contacts_requires_login(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = build_client(monkeypatch)

    response = client.get("/api/contacts?query=ex")

    assert response.status_code == 401


def test_contacts_query_filters_recent_contacts_and_deduplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    client, fake_redis = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    from app.contacts import record_recent_contacts

    session_cookie = client.cookies.get("webmail_session")
    assert session_cookie
    session = SessionStore(client=fake_redis, settings=FakeSettings()).get(session_cookie)
    assert session
    from app.auth import AuthSession

    auth_session = AuthSession(
        session_id=session_cookie,
        email=str(session["email"]),
        password=str(session["secret"]),
        imap=dict(session.get("imap") or {}),
        smtp=dict(session.get("smtp") or {}),
        preferences={},
    )
    record_recent_contacts(auth_session, ["Alice@example.com", "alice@example.com", "bob@example.com"])
    fake_redis.zadd("contacts:recent:user@example.com", {"carol@example.com": 200.0})

    response = client.get("/api/contacts", params={"query": "ali"})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    contacts = body["data"]["contacts"]
    assert len(contacts) == 1
    assert contacts[0]["email"] == "alice@example.com"


def test_contacts_limit_caps_results(monkeypatch: pytest.MonkeyPatch) -> None:
    client, fake_redis = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    for index in range(12):
        fake_redis.zadd(
            "contacts:recent:user@example.com",
            {f"person{index}@example.com": float(index + 1)},
        )

    response = client.get("/api/contacts", params={"query": "person", "limit": 10})

    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]["contacts"]) == 10
