from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from types import ModuleType

import fakeredis
import pydantic.networks as pydantic_networks
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine


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
    appended_messages: list[tuple[str, object]] = []

    def __init__(self, settings) -> None:
        self.settings = settings

    @classmethod
    def reset(cls) -> None:
        cls.appended_messages = []

    def connect(self):
        return self

    def login(self):
        from app.mail_adapters import MailAdapterError

        if self.settings.password == "wrong-password":
            raise MailAdapterError("IMAP 登录失败", operation="login")
        return self

    def list_folders(self):
        return ['(\\HasNoChildren) "/" "INBOX"', '(\\HasNoChildren) "/" "Sent"']

    def append_message(self, folder: str, message) -> None:
        self.appended_messages.append((folder, message))

    def logout(self):
        return self


class FakeSmtpAdapter:
    sent_messages: list[object] = []

    def __init__(self, settings) -> None:
        self.settings = settings

    @classmethod
    def reset(cls) -> None:
        cls.sent_messages = []

    def connect(self):
        return self

    def login(self):
        return self

    def send_message(self, message) -> None:
        self.sent_messages.append(message)

    def quit(self):
        return self


def _purge_app_modules() -> None:
    for module_name in list(sys.modules):
        if module_name.startswith("app.") and module_name in {
            "app.auth",
            "app.main",
            "app.contacts",
            "app.mail_state",
            "app.compose",
            "app.mailbox",
            "app.attachments",
            "app.drafts",
            "app.signatures",
        }:
            sys.modules.pop(module_name, None)


def build_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    settings = FakeSettings(f"sqlite+pysqlite:///{tmp_path / 'contacts.sqlite3'}")
    FakeSmtpAdapter.reset()
    FakeImapAdapter.reset()

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
    monkeypatch.setattr(mail_adapters_module, "SmtpAdapter", FakeSmtpAdapter)

    _purge_app_modules()
    models_module = importlib.import_module("app.models")
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_module, "get_engine", lambda database_url=None: engine)
    models_module.Base.metadata.drop_all(engine)
    models_module.Base.metadata.create_all(engine)

    auth_module = importlib.import_module("app.auth")
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings, raising=False)
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: fake_redis, raising=False)
    monkeypatch.setattr(auth_module, "ImapAdapter", FakeImapAdapter, raising=False)
    monkeypatch.setattr(auth_module, "SmtpAdapter", FakeSmtpAdapter, raising=False)

    main_module = importlib.import_module("app.main")
    monkeypatch.setattr(main_module, "get_settings", lambda: settings, raising=False)
    client = TestClient(main_module.app, raise_server_exceptions=False)
    setattr(client, "_fake_redis", fake_redis)
    return client


def login(client: TestClient, email: str, password: str):
    response = client.post("/api/auth/login", json={"email": email, "password": password, "remember": False})
    csrf_token = client.cookies.get("webmail_csrf")
    if response.status_code == 200 and csrf_token:
        client.headers.update({"X-CSRF-Token": csrf_token})
    return response


def _get_models():
    from app.models import MailContact, MailContactTag

    return MailContact, MailContactTag


def _ensure_seed_contact(client: TestClient, email: str, *, display_name: str, group_name: str | None, tags: list[str], is_favorite: bool = False) -> dict[str, object]:
    response = client.post(
        "/api/contacts",
        json={
            "email": email,
            "display_name": display_name,
            "group_name": group_name,
            "company": "OpenAI",
            "phone": "13800000000",
            "notes": "测试备注",
            "is_favorite": is_favorite,
            "is_blacklisted": False,
            "is_whitelisted": False,
            "tags": tags,
        },
    )
    assert response.status_code == 200
    return response.json()["data"]["contact"]


def test_contact_model_unique_constraint_and_update_timestamp(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    build_client(monkeypatch, tmp_path)
    MailContact, MailContactTag = _get_models()

    assert MailContact.__tablename__ == "mail_contacts"
    assert MailContactTag.__tablename__ == "mail_contact_tags"
    assert any({column.name for column in constraint.columns} == {"account_id", "email"} for constraint in MailContact.__table__.constraints if hasattr(constraint, "columns"))
    assert any({column.name for column in constraint.columns} == {"contact_id", "name"} for constraint in MailContactTag.__table__.constraints if hasattr(constraint, "columns"))
    assert MailContact.__table__.c.updated_at.onupdate is not None
    assert MailContactTag.__table__.c.created_at.type.timezone is True
    assert "is_blacklisted" in MailContact.__table__.columns
    assert "is_whitelisted" in MailContact.__table__.columns


def test_whitelist_priority_state_can_be_resolved(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client = build_client(monkeypatch, tmp_path)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    created = _ensure_seed_contact(
        client,
        "vip@example.com",
        display_name="VIP",
        group_name="重要客户",
        tags=["白名单"],
    )

    update_response = client.patch(
        f"/api/contacts/{created['id']}",
        json={
            "is_blacklisted": True,
            "is_whitelisted": True,
        },
    )
    assert update_response.status_code == 200

    from app.auth import AuthSession
    from app.contacts import get_contact_rule_state

    session_cookie = client.cookies.get("webmail_session")
    assert session_cookie
    session = AuthSession(
        session_id=session_cookie,
        email="user@example.com",
        password="correct-password",
        imap={},
        smtp={},
        preferences={},
    )

    state = get_contact_rule_state(session, "vip@example.com")
    assert state["is_blacklisted"] is True
    assert state["is_whitelisted"] is True
    assert state["effective_rule"] == "whitelist"


def test_contacts_requires_login(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client = build_client(monkeypatch, tmp_path)
    response = client.get("/api/contacts")
    assert response.status_code == 401


def test_contacts_crud_group_and_tag_flow(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client = build_client(monkeypatch, tmp_path)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    created = _ensure_seed_contact(
        client,
        "alice@example.com",
        display_name="Alice",
        group_name="朋友",
        tags=["vip", "客户"],
    )
    assert created["email"] == "alice@example.com"
    assert created["group_name"] == "朋友"
    assert {tag["name"] for tag in created["tags"]} == {"客户", "vip"}

    detail_response = client.get(f"/api/contacts/{created['id']}")
    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["contact"]["email"] == "alice@example.com"

    update_response = client.patch(
        f"/api/contacts/{created['id']}",
        json={
            "display_name": "Alice Updated",
            "group_name": "工作",
            "company": "OpenAI Research",
            "phone": "13900000000",
            "notes": "更新后的备注",
            "is_favorite": True,
            "is_blacklisted": True,
            "is_whitelisted": True,
            "tags": ["vip", "重点"],
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()["data"]["contact"]
    assert updated["display_name"] == "Alice Updated"
    assert updated["group_name"] == "工作"
    assert updated["is_favorite"] is True
    assert updated["is_blacklisted"] is True
    assert updated["is_whitelisted"] is True
    assert {tag["name"] for tag in updated["tags"]} == {"vip", "重点"}

    blacklist_response = client.get("/api/contacts/blacklist")
    assert blacklist_response.status_code == 200
    assert blacklist_response.json()["data"]["contacts"] == ["alice@example.com"]


def test_whitelist_priority_state_can_be_persisted(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client = build_client(monkeypatch, tmp_path)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    created = _ensure_seed_contact(
        client,
        "vip@example.com",
        display_name="VIP",
        group_name="重要客户",
        tags=["白名单"],
    )

    update_response = client.patch(
        f"/api/contacts/{created['id']}",
        json={
            "is_blacklisted": True,
            "is_whitelisted": True,
        },
    )

    assert update_response.status_code == 200
    updated = update_response.json()["data"]["contact"]
    assert updated["is_blacklisted"] is True
    assert updated["is_whitelisted"] is True

    delete_response = client.delete(f"/api/contacts/{created['id']}")
    assert delete_response.status_code == 200
    assert delete_response.json()["data"]["deleted"] is True


def test_contacts_list_supports_pagination_search_group_and_tag_filters(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client = build_client(monkeypatch, tmp_path)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    _ensure_seed_contact(client, "alice@example.com", display_name="Alice", group_name="朋友", tags=["vip"], is_favorite=True)
    _ensure_seed_contact(client, "bob@example.com", display_name="Bob", group_name="工作", tags=["客户"])
    _ensure_seed_contact(client, "carol@example.com", display_name="Carol", group_name="朋友", tags=["vip", "客户"])

    page_response = client.get("/api/contacts", params={"query": "o", "page": 1, "page_size": 2})
    assert page_response.status_code == 200
    page_body = page_response.json()["data"]
    assert page_body["page"] == 1
    assert page_body["page_size"] == 2
    assert page_body["total"] == 3

    group_response = client.get("/api/contacts", params={"group_name": "朋友"})
    assert group_response.status_code == 200
    group_contacts = group_response.json()["data"]["contacts"]
    assert {item["email"] for item in group_contacts} == {"alice@example.com", "carol@example.com"}

    tag_response = client.get("/api/contacts", params={"tag": "vip"})
    assert tag_response.status_code == 200
    tag_contacts = tag_response.json()["data"]["contacts"]
    assert {item["email"] for item in tag_contacts} == {"alice@example.com", "carol@example.com"}


def test_send_mail_auto_records_contacts_and_refreshes_timestamp(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client = build_client(monkeypatch, tmp_path)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    contacts_module = importlib.import_module("app.contacts")
    timestamps = iter(
        [
            datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc),
        ]
    )
    monkeypatch.setattr(contacts_module, "_contact_used_at", lambda: next(timestamps))

    first_response = client.post(
        "/api/messages/send",
        json={
            "to": ["receiver@example.com"],
            "cc": ["cc@example.com"],
            "bcc": ["bcc@example.com"],
            "subject": "第一次发送",
            "text_body": "正文一",
            "attachment_ids": [],
        },
    )
    assert first_response.status_code == 200

    second_response = client.post(
        "/api/messages/send",
        json={
            "to": ["receiver@example.com"],
            "subject": "第二次发送",
            "text_body": "正文二",
            "attachment_ids": [],
        },
    )
    assert second_response.status_code == 200

    fake_redis = getattr(client, "_fake_redis")
    recent_contacts_key = "contacts:recent:user@example.com"
    assert fake_redis.zcard(recent_contacts_key) == 3
    assert fake_redis.zscore(recent_contacts_key, "receiver@example.com") == datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc).timestamp()
    assert fake_redis.zscore(recent_contacts_key, "cc@example.com") is not None
    assert fake_redis.zscore(recent_contacts_key, "bcc@example.com") is not None
