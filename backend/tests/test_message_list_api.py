from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, parsedate_to_datetime
from types import ModuleType

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


def make_settings(*, login_fail_limit: int = 5) -> FakeSettings:
    return FakeSettings(login_fail_limit=login_fail_limit)


def make_email_message(
    *,
    uid: str,
    sender_name: str,
    sender_email: str,
    subject: str,
    body: str,
    sent_at: datetime,
    read: bool,
    has_attachments: bool,
) -> dict[str, object]:
    message = EmailMessage()
    message["Message-ID"] = f"<{uid}@example.com>"
    message["From"] = f"{sender_name} <{sender_email}>"
    message["To"] = "recipient@example.com"
    message["Subject"] = subject
    message["Date"] = format_datetime(sent_at)
    message.set_content(body)

    if has_attachments:
        message.add_attachment(
            b"attachment-bytes",
            maintype="application",
            subtype="octet-stream",
            filename="invoice.pdf",
        )

    return {
        "uid": uid,
        "sender_name": sender_name,
        "sender_email": sender_email,
        "subject": subject,
        "body": body,
        "sent_at": sent_at,
        "read": read,
        "has_attachments": has_attachments,
        "raw": message.as_bytes(),
    }


def make_mailbox_state() -> dict[str, object]:
    base_time = datetime(2026, 5, 7, 9, 0, tzinfo=UTC)
    return {
        "mailbox": {
            "INBOX": [
                make_email_message(
                    uid="103",
                    sender_name="Carol",
                    sender_email="carol@example.com",
                    subject="最新邮件",
                    body="第三封邮件的摘要内容。\n后续正文不影响摘要。",
                    sent_at=base_time,
                    read=True,
                    has_attachments=False,
                ),
                make_email_message(
                    uid="102",
                    sender_name="Bob",
                    sender_email="bob@example.com",
                    subject="第二封邮件",
                    body="第二封邮件的摘要内容。\n这里还有更多正文。",
                    sent_at=base_time - timedelta(minutes=5),
                    read=False,
                    has_attachments=True,
                ),
                make_email_message(
                    uid="101",
                    sender_name="Alice",
                    sender_email="alice@example.com",
                    subject="第一封邮件",
                    body="第一封邮件的摘要内容。\n更多正文。",
                    sent_at=base_time - timedelta(minutes=10),
                    read=False,
                    has_attachments=False,
                ),
            ]
        },
        "fetch_count": 0,
    }


def add_mailbox_message(mailbox_state: dict[str, object], folder: str, message: dict[str, object]) -> None:
    mailbox = mailbox_state["mailbox"]
    assert isinstance(mailbox, dict)
    folder_messages = mailbox.setdefault(folder, [])
    assert isinstance(folder_messages, list)
    folder_messages.append(message)
    folder_messages.sort(key=lambda item: item["sent_at"], reverse=True)


def build_client(
    monkeypatch: pytest.MonkeyPatch,
    mailbox_state: dict[str, object],
    *,
    login_fail_limit: int = 5,
) -> TestClient:
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

    class FakeImapAdapter:
        def __init__(self, settings_obj) -> None:
            self.settings = settings_obj
            self.connected = False
            self.logged_in = False
            self.logged_out = False
            self.selected_folder = "INBOX"

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

        def select_folder(self, folder: str):
            self.selected_folder = folder
            return "OK", [b"3"]

        def search_uids(self, criteria):
            mailbox = mailbox_state["mailbox"]
            assert isinstance(mailbox, dict)
            messages = mailbox.get(self.selected_folder, [])
            assert isinstance(messages, list)
            sorted_messages = sorted(messages, key=lambda item: item["sent_at"], reverse=True)
            return [str(item["uid"]) for item in sorted_messages]

        def fetch_message_bytes(self, uid: str | bytes):
            mailbox_state["fetch_count"] = int(mailbox_state["fetch_count"]) + 1
            uid_text = uid.decode() if isinstance(uid, bytes) else str(uid)
            mailbox = mailbox_state["mailbox"]
            assert isinstance(mailbox, dict)
            messages = mailbox.get(self.selected_folder, [])
            assert isinstance(messages, list)
            for item in messages:
                if str(item["uid"]) == uid_text:
                    return item["raw"]
            raise MailAdapterError("IMAP 邮件不存在", operation="fetch_message_bytes")

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
    sys.modules.pop("app.main", None)
    auth_module = importlib.import_module("app.auth")
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings, raising=False)
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: fake_redis, raising=False)
    monkeypatch.setattr(auth_module, "ImapAdapter", FakeImapAdapter)

    main_module = importlib.import_module("app.main")
    return TestClient(main_module.app, raise_server_exceptions=False)


def login(client: TestClient, email: str, password: str, *, remember: bool = False):
    return client.post(
        "/api/auth/login",
        json={
            "email": email,
            "password": password,
            "remember": remember,
        },
    )


def request_message_list(
    client: TestClient,
    folder: str,
    *,
    page: int = 1,
    page_size: int = 30,
    refresh: bool = False,
):
    return client.get(
        f"/api/folders/{folder}/messages",
        params={
            "page": page,
            "page_size": page_size,
            "refresh": str(refresh).lower(),
        },
    )


def extract_message_rows(body: dict[str, object]) -> list[dict[str, object]]:
    data = body["data"]
    assert isinstance(data, dict)
    rows = data.get("messages") or data.get("items")
    assert isinstance(rows, list)
    assert rows
    return rows


def parse_response_datetime(value: object) -> datetime:
    assert isinstance(value, str)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return parsedate_to_datetime(value)


def assert_message_row_shape(row: dict[str, object]) -> None:
    assert {"uid", "sender", "subject", "date", "read", "has_attachments", "snippet"} <= set(row)
    assert isinstance(row["uid"], str)
    assert row["subject"]
    assert row["snippet"]
    assert isinstance(row["read"], bool)
    assert isinstance(row["has_attachments"], bool)


def test_message_list_requires_login(monkeypatch: pytest.MonkeyPatch) -> None:
    mailbox_state = make_mailbox_state()
    client = build_client(monkeypatch, mailbox_state)

    response = request_message_list(client, "INBOX")

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_message_list_orders_by_date_desc_and_returns_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    mailbox_state = make_mailbox_state()
    client = build_client(monkeypatch, mailbox_state)

    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    response = request_message_list(client, "INBOX", page=1, page_size=2)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    data = body["data"]
    assert isinstance(data, dict)
    assert data["folder"] == "INBOX"
    assert data["page"] == 1
    assert data["page_size"] == 2

    rows = extract_message_rows(body)
    assert [row["uid"] for row in rows] == ["103", "102"]
    assert parse_response_datetime(rows[0]["date"]) > parse_response_datetime(rows[1]["date"])

    for row in rows:
        assert_message_row_shape(row)


def test_message_list_second_identical_request_uses_redis_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    mailbox_state = make_mailbox_state()
    client = build_client(monkeypatch, mailbox_state)

    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    first_response = request_message_list(client, "INBOX", page=1, page_size=2)
    assert first_response.status_code == 200
    first_body = first_response.json()
    first_rows = extract_message_rows(first_body)
    first_fetch_count = int(mailbox_state["fetch_count"])

    second_response = request_message_list(client, "INBOX", page=1, page_size=2)
    assert second_response.status_code == 200
    second_body = second_response.json()
    second_rows = extract_message_rows(second_body)

    assert int(mailbox_state["fetch_count"]) == first_fetch_count
    assert second_rows == first_rows


def test_message_list_refresh_bypasses_cache_and_reveals_new_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    mailbox_state = make_mailbox_state()
    client = build_client(monkeypatch, mailbox_state)

    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    initial_response = request_message_list(client, "INBOX", page=1, page_size=2)
    assert initial_response.status_code == 200
    initial_rows = extract_message_rows(initial_response.json())
    initial_fetch_count = int(mailbox_state["fetch_count"])

    add_mailbox_message(
        mailbox_state,
        "INBOX",
        make_email_message(
            uid="104",
            sender_name="Dora",
            sender_email="dora@example.com",
            subject="刷新后新邮件",
            body="刷新后才出现的邮件摘要。\n正文内容不影响列表摘要。",
            sent_at=datetime(2026, 5, 7, 9, 3, tzinfo=UTC),
            read=False,
            has_attachments=False,
        ),
    )

    refresh_response = request_message_list(client, "INBOX", page=1, page_size=2, refresh=True)
    assert refresh_response.status_code == 200
    refresh_body = refresh_response.json()
    refresh_rows = extract_message_rows(refresh_body)

    assert int(mailbox_state["fetch_count"]) > initial_fetch_count
    assert refresh_rows[0]["uid"] == "104"
    assert refresh_rows[0] != initial_rows[0]
    assert_message_row_shape(refresh_rows[0])
