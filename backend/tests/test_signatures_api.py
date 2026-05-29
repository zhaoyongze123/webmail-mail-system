from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import fakeredis
import pydantic.networks as pydantic_networks
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


class FakeSettings:
    def __init__(self, database_url: str) -> None:
        self.app_env = "test"
        self.app_name = "webmail-mvp"
        self.app_secret_key = "test-secret"
        self.cors_origins = "http://localhost:5173,http://127.0.0.1:5173"
        self.database_url = database_url
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
            from app.mail_adapters import MailAdapterError

            raise MailAdapterError("IMAP 登录失败", operation="login")
        return self

    def logout(self):
        return self


def _purge_app_modules() -> None:
    for module_name in list(sys.modules):
        if module_name.startswith("app.") and module_name in {
            "app.auth",
            "app.main",
            "app.signatures",
            "app.mail_state",
            "app.mail_preferences",
            "app.mailbox",
            "app.compose",
            "app.attachments",
            "app.drafts",
            "app.contacts",
        }:
            sys.modules.pop(module_name, None)


def build_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'signatures.sqlite3'}"
    settings = FakeSettings(database_url)

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

    for module_name in [
        "app.config",
        "app.cache",
        "app.redis_client",
        "app.db",
        "app.models",
        "app.observability",
        "app.security",
        "app.mail_directory",
        "app.auth",
        "app.main",
        "app.signatures",
        "app.mail_state",
        "app.mail_preferences",
        "app.mailbox",
        "app.compose",
        "app.attachments",
        "app.drafts",
        "app.contacts",
    ]:
        sys.modules.pop(module_name, None)

    config_module = importlib.import_module("app.config")
    cache_module = importlib.import_module("app.cache")
    redis_client_module = importlib.import_module("app.redis_client")
    db_module = importlib.import_module("app.db")
    mail_adapters_module = importlib.import_module("app.mail_adapters")

    config_module.get_settings.cache_clear()
    redis_client_module.get_redis_client.cache_clear()
    db_module.get_engine.cache_clear()

    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_module, "get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(redis_client_module, "get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(mail_adapters_module, "ImapAdapter", FakeImapAdapter)

    sys.modules.pop("app.mail_preferences", None)
    _purge_app_modules()
    models_module = importlib.import_module("app.models")
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(db_module, "get_engine", lambda database_url=None: engine)
    models_module.MailUserPreference.__table__.drop(engine, checkfirst=True)
    models_module.MailAccount.__table__.drop(engine, checkfirst=True)
    models_module.MailSignature.__table__.drop(engine, checkfirst=True)
    models_module.MailAccount.__table__.create(engine, checkfirst=True)
    models_module.MailSignature.__table__.create(engine, checkfirst=True)
    models_module.MailUserPreference.__table__.create(engine, checkfirst=True)

    auth_module = importlib.import_module("app.auth")
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings, raising=False)
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: fake_redis, raising=False)
    monkeypatch.setattr(auth_module, "ImapAdapter", FakeImapAdapter, raising=False)

    main_module = importlib.import_module("app.main")
    monkeypatch.setattr(main_module, "get_settings", lambda: settings, raising=False)
    return TestClient(main_module.app, raise_server_exceptions=False)


def login(client: TestClient, email: str, password: str):
    response = client.post("/api/auth/login", json={"email": email, "password": password, "remember": False})
    csrf_token = client.cookies.get("webmail_csrf")
    if response.status_code == 200 and csrf_token:
        client.headers.update({"X-CSRF-Token": csrf_token})
    return response


def test_signatures_require_login(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = build_client(monkeypatch, tmp_path)

    response = client.get("/api/signatures")

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_signature_crud_and_default_switching(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = build_client(monkeypatch, tmp_path)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    empty_default_response = client.get("/api/signatures/default")
    assert empty_default_response.status_code == 200
    assert empty_default_response.json()["data"]["signature"] is None

    create_first_response = client.post(
        "/api/signatures",
        json={
            "name": "默认签名",
            "content": "第一版签名",
            "is_default": False,
        },
    )
    assert create_first_response.status_code == 200
    first_signature = create_first_response.json()["data"]["signature"]
    assert first_signature["name"] == "默认签名"
    assert first_signature["is_default"] is True

    create_second_response = client.post(
        "/api/signatures",
        json={
            "name": "工作签名",
            "content": "第二版签名",
            "is_default": False,
        },
    )
    assert create_second_response.status_code == 200
    second_signature = create_second_response.json()["data"]["signature"]
    assert second_signature["is_default"] is False

    list_response = client.get("/api/signatures")
    assert list_response.status_code == 200
    signatures = list_response.json()["data"]["signatures"]
    assert [item["name"] for item in signatures] == ["默认签名", "工作签名"]
    assert signatures[0]["is_default"] is True
    assert signatures[1]["is_default"] is False

    update_response = client.patch(
        f"/api/signatures/{second_signature['id']}",
        json={
            "name": "工作签名-更新",
            "content": "第二版签名已更新",
        },
    )
    assert update_response.status_code == 200
    updated_signature = update_response.json()["data"]["signature"]
    assert updated_signature["name"] == "工作签名-更新"
    assert updated_signature["content"] == "第二版签名已更新"

    default_response = client.post(f"/api/signatures/{second_signature['id']}/default")
    assert default_response.status_code == 200
    default_signature = default_response.json()["data"]["signature"]
    assert default_signature["id"] == second_signature["id"]
    assert default_signature["is_default"] is True

    refreshed_list_response = client.get("/api/signatures")
    assert refreshed_list_response.status_code == 200
    refreshed_signatures = refreshed_list_response.json()["data"]["signatures"]
    assert refreshed_signatures[0]["id"] == second_signature["id"]
    assert refreshed_signatures[0]["is_default"] is True
    assert refreshed_signatures[1]["is_default"] is False

    delete_response = client.delete(f"/api/signatures/{second_signature['id']}")
    assert delete_response.status_code == 200
    assert delete_response.json()["data"]["deleted"] is True

    after_delete_default_response = client.get("/api/signatures/default")
    assert after_delete_default_response.status_code == 200
    after_delete_default = after_delete_default_response.json()["data"]["signature"]
    assert after_delete_default["id"] == first_signature["id"]
    assert after_delete_default["is_default"] is True


def test_signature_not_found_returns_404(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = build_client(monkeypatch, tmp_path)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    response = client.patch(
        "/api/signatures/11111111-1111-1111-1111-111111111111",
        json={"name": "不存在的签名", "content": "内容"},
    )

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "SIGNATURE_NOT_FOUND"
