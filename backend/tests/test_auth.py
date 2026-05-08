import importlib
import sys
from types import ModuleType

import fakeredis
import pytest
from fastapi.testclient import TestClient
import pydantic.networks as pydantic_networks

from app.mail_adapters import MailAdapterError
from app.observability import get_recent_audit_events, reset_observability_state


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


def build_client(monkeypatch: pytest.MonkeyPatch, *, login_fail_limit: int = 5) -> TestClient:
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

    sys.modules.pop("app.auth", None)
    sys.modules.pop("app.main", None)
    auth_module = importlib.import_module("app.auth")
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings, raising=False)
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: fake_redis, raising=False)
    monkeypatch.setattr(auth_module, "ImapAdapter", FakeImapAdapter)

    main_module = importlib.import_module("app.main")
    reset_observability_state()
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


def test_login_success_sets_session_cookie_and_me_returns_email(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)

    response = login(client, "user@example.com", "correct-password")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["email"] == "user@example.com"
    assert response.headers["X-Request-ID"].startswith("req_")
    set_cookie = response.headers.get("set-cookie", "")
    assert "webmail_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert any(event["event_type"] == "auth.login" and event["success"] is True for event in get_recent_audit_events())

    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 200
    me_body = me_response.json()
    assert me_body["success"] is True
    assert me_body["error"] is None
    assert me_body["data"]["email"] == "user@example.com"


def test_register_success_sets_session_cookie_and_me_returns_email(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)

    response = client.post(
        "/api/auth/register",
        json={
            "email": "new-user@example.com",
            "password": "correct-password",
            "display_name": "新用户",
            "remember": True,
        },
    )
    csrf_token = client.cookies.get("webmail_csrf")
    if csrf_token:
        client.headers.update({"X-CSRF-Token": csrf_token})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["email"] == "new-user@example.com"
    assert "webmail_session=" in response.headers.get("set-cookie", "")
    assert any(event["event_type"] == "auth.register" and event["success"] is True for event in get_recent_audit_events())

    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["data"]["email"] == "new-user@example.com"


def test_logout_invalidates_session(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)

    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 200
    logout_body = logout_response.json()
    assert logout_body["success"] is True
    assert logout_body["error"] is None
    assert any(event["event_type"] == "auth.logout" for event in get_recent_audit_events())

    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 401
    me_body = me_response.json()
    assert me_body["success"] is False
    assert me_body["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_login_wrong_password_returns_invalid_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)

    response = login(client, "user@example.com", "wrong-password")

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "AUTH_INVALID_CREDENTIALS"
    assert body["error"]["details"] == {}
    assert any(event["event_type"] == "auth.login" and event["success"] is False for event in get_recent_audit_events())


def test_login_repeated_failures_trigger_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch, login_fail_limit=1)

    first_response = login(client, "user@example.com", "wrong-password")
    assert first_response.status_code in {401, 429}
    first_body = first_response.json()
    assert first_body["success"] is False
    assert first_body["error"]["code"] in {"AUTH_INVALID_CREDENTIALS", "AUTH_RATE_LIMITED"}

    second_response = login(client, "user@example.com", "wrong-password")
    assert second_response.status_code == 429
    second_body = second_response.json()
    assert second_body["success"] is False
    assert second_body["error"]["code"] == "AUTH_RATE_LIMITED"
    assert any(event["event_type"] == "auth.login.rate_limited" for event in get_recent_audit_events())


def test_request_log_does_not_expose_password(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    client = build_client(monkeypatch)

    with caplog.at_level("INFO"):
        response = login(client, "user@example.com", "correct-password")

    assert response.status_code == 200
    log_blob = "\n".join(record.getMessage() for record in caplog.records)
    assert "correct-password" not in log_blob
    assert '"path":"/api/auth/login"' in log_blob
    assert '"request_id":"' in log_blob
