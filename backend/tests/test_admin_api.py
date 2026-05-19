from __future__ import annotations

import importlib
import sys
from types import ModuleType

import fakeredis
import pydantic.networks as pydantic_networks
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


class FakeSettings:
    def __init__(self) -> None:
        self.app_env = "test"
        self.app_name = "webmail-mvp"
        self.app_secret_key = "test-secret"
        self.admin_jwt_secret = "admin-secret"
        self.database_url = "sqlite+pysqlite:///:memory:"
        self.redis_url = "redis://localhost:6379/15"
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
        self.admin_access_token_ttl_minutes = 15
        self.admin_refresh_token_ttl_days = 7
        self.admin_bootstrap_username = "admin@example.com"
        self.admin_bootstrap_password = "Admin123456!"
        self.admin_totp_issuer = "Webmail Admin"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def effective_admin_jwt_secret(self) -> str:
        return self.admin_jwt_secret or self.app_secret_key

    @property
    def effective_admin_bootstrap_username(self) -> str | None:
        return self.admin_bootstrap_username

    @property
    def effective_admin_bootstrap_password(self) -> str | None:
        return self.admin_bootstrap_password


def build_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
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
    db_module = importlib.import_module("app.db")

    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_module, "get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(redis_client_module, "get_redis_client", lambda: fake_redis)

    for module_name in [
        "app.mail_state",
        "app.mail_preferences",
        "app.mailbox",
        "app.contacts",
        "app.signatures",
        "app.auth",
        "app.main",
        "app.admin_auth",
        "app.admin_api",
    ]:
        sys.modules.pop(module_name, None)

    models_module = importlib.import_module("app.models")
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(db_module, "get_engine", lambda database_url=None: engine)
    models_module.Base.metadata.create_all(engine)

    main_module = importlib.import_module("app.main")
    monkeypatch.setattr(main_module, "get_settings", lambda: settings, raising=False)
    importlib.import_module("app.observability").reset_observability_state()
    return TestClient(main_module.app, raise_server_exceptions=False)


def admin_login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/admin/auth/login",
        json={"email": "admin@example.com", "password": "Admin123456!"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    return body["data"]


def auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def test_admin_login_and_me(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)

    payload = admin_login(client)
    me_response = client.get("/api/admin/auth/me", headers=auth_headers(payload["access_token"]))

    assert me_response.status_code == 200
    body = me_response.json()
    assert body["success"] is True
    assert body["data"]["email"] == "admin@example.com"
    assert body["data"]["role"] == "superadmin"


def test_admin_refresh_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    payload = admin_login(client)

    response = client.post("/api/admin/auth/refresh", json={"refresh_token": payload["refresh_token"]})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"]


def test_admin_overview_domains_and_health(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    payload = admin_login(client)
    headers = auth_headers(payload["access_token"])

    create_domain_response = client.post(
        "/api/admin/domains",
        headers=headers,
        json={"name": "example.com", "quota_limit_mb": 2048, "status": "active"},
    )
    assert create_domain_response.status_code == 200

    domains_response = client.get("/api/admin/domains", headers=headers)
    assert domains_response.status_code == 200
    domains_data = domains_response.json()["data"]
    assert domains_data["items"][0]["name"] == "example.com"

    overview_response = client.get("/api/admin/overview", headers=headers)
    assert overview_response.status_code == 200
    overview = overview_response.json()["data"]
    assert overview["mail_domains"] >= 1
    assert "recent_audits" in overview

    health_response = client.get("/api/admin/system-health", headers=headers)
    assert health_response.status_code == 200
    health_data = health_response.json()["data"]
    assert len(health_data["items"]) == 3
    assert health_data["items"][0]["status"] in {"ok", "down"}


def test_admin_users_and_aliases_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    payload = admin_login(client)
    headers = auth_headers(payload["access_token"])

    domain_response = client.post(
        "/api/admin/domains",
        headers=headers,
        json={"name": "mail.test", "quota_limit_mb": 4096, "status": "active"},
    )
    domain_id = domain_response.json()["data"]["domain"]["id"]

    user_response = client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "alice@mail.test",
            "display_name": "Alice",
            "domain_id": domain_id,
            "password": "User123456!",
            "quota_mb": 600,
            "status": "active",
            "is_admin": False,
        },
    )
    assert user_response.status_code == 200
    user_id = user_response.json()["data"]["user"]["id"]

    alias_response = client.post(
        "/api/admin/aliases",
        headers=headers,
        json={
            "domain_id": domain_id,
            "source_address": "sales@mail.test",
            "target_addresses": ["alice@mail.test"],
        },
    )
    assert alias_response.status_code == 200

    quotas_response = client.get("/api/admin/quotas", headers=headers)
    assert quotas_response.status_code == 200
    assert quotas_response.json()["data"]["items"][0]["used_quota_mb"] >= 600

    update_quota_response = client.patch(
        f"/api/admin/users/{user_id}/quota",
        headers=headers,
        json={"quota_mb": 700},
    )
    assert update_quota_response.status_code == 200
    assert update_quota_response.json()["data"]["user"]["quota_mb"] == 700
