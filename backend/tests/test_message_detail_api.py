from __future__ import annotations

import importlib
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

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


@dataclass
class FakeMailboxMessage:
    folder: str
    uid: str
    raw_bytes: bytes
    flags: set[str] = field(default_factory=set)


class FakeImapAdapter:
    mailboxes: dict[tuple[str, str], FakeMailboxMessage] = {}
    login_calls: list[tuple[str, str]] = []
    store_calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    selected_folders: list[str] = []
    last_instance: "FakeImapAdapter | None" = None

    def __init__(self, settings) -> None:
        self.settings = settings
        self.connected = False
        self.logged_in = False
        self.logged_out = False
        self.selected_folder: str | None = None
        self.last_fetch_uid: str | None = None
        FakeImapAdapter.last_instance = self

    @classmethod
    def reset(cls) -> None:
        cls.mailboxes = {}
        cls.login_calls = []
        cls.store_calls = []
        cls.selected_folders = []
        cls.last_instance = None

    @classmethod
    def seed_message(cls, folder: str, uid: str, raw_bytes: bytes) -> None:
        cls.mailboxes[(folder, uid)] = FakeMailboxMessage(folder=folder, uid=uid, raw_bytes=raw_bytes)

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
        FakeImapAdapter.selected_folders.append(folder)
        return "OK", [b"1"]

    def fetch_message_bytes(self, uid: str | bytes) -> bytes:
        uid_str = uid.decode("utf-8") if isinstance(uid, bytes) else str(uid)
        self.last_fetch_uid = uid_str
        if self.selected_folder is None:
            raise MailAdapterError("未选择文件夹", operation="fetch_message_bytes")
        message = self.mailboxes.get((self.selected_folder, uid_str))
        if message is None:
            raise MailAdapterError("IMAP 邮件内容为空", operation="fetch_message_bytes")
        return message.raw_bytes

    def fetch(self, uid: str | bytes, query: str):
        payload = self.fetch_message_bytes(uid)
        return "OK", [(b"RFC822", payload)]

    def store(self, uid: str | bytes, command: str, flags: str):
        uid_str = uid.decode("utf-8") if isinstance(uid, bytes) else str(uid)
        FakeImapAdapter.store_calls.append((uid_str, (command, flags), {"folder": self.selected_folder}))
        if self.selected_folder is not None and (self.selected_folder, uid_str) in self.mailboxes:
            self.mailboxes[(self.selected_folder, uid_str)].flags.add(flags)
        return "OK", [b"FLAGS"]

    def uid_search(self, criteria: str):
        if self.selected_folder is None:
            return []
        return [uid for (folder, uid), _message in self.mailboxes.items() if folder == self.selected_folder]

    def uid_fetch_message_bytes(self, uid: str | bytes) -> bytes:
        return self.fetch_message_bytes(uid)

    def mark_seen(self, uid: str | bytes):
        self.store(uid, "+FLAGS", "\\Seen")
        return self

    def status(self, folder: str):
        unread = 0
        total = 0
        for (candidate_folder, _uid), message in self.mailboxes.items():
            if candidate_folder != folder:
                continue
            total += 1
            if "\\Seen" not in message.flags:
                unread += 1
        return {"UNSEEN": unread, "MESSAGES": total, "UIDVALIDITY": 1}


def make_settings(*, login_fail_limit: int = 5) -> FakeSettings:
    return FakeSettings(login_fail_limit=login_fail_limit)


def _make_message_bytes(
    *,
    subject: str,
    sender_name: str,
    sender_email: str,
    to_emails: list[str],
    cc_emails: list[str],
    date_value,
    text_body: str | None = None,
    html_body: str | None = None,
    attachments: list[tuple[str, str, bytes]] | None = None,
) -> bytes:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{sender_name} <{sender_email}>"
    message["To"] = ", ".join(to_emails)
    if cc_emails:
        message["Cc"] = ", ".join(cc_emails)
    message["Date"] = format_datetime(date_value)
    message["Message-ID"] = "<msg-001@example.com>"
    if text_body is not None and html_body is not None:
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")
    elif html_body is not None:
        message.add_alternative(html_body, subtype="html")
    else:
        message.set_content(text_body or "")
    for filename, content_type, content in attachments or []:
        maintype, subtype = content_type.split("/", 1)
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    return message.as_bytes(policy=policy.default)


def build_client(monkeypatch: pytest.MonkeyPatch, *, login_fail_limit: int = 5) -> TestClient:
    FakeImapAdapter.reset()
    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    settings = make_settings(login_fail_limit=login_fail_limit)
    bleach_module = ModuleType("bleach")

    def clean(value: str, *, tags=None, attributes=None, protocols=None, strip=False):
        cleaned = re.sub(r"(?is)<script.*?>.*?</script>", "", value)
        cleaned = re.sub(r'\son\w+="[^"]*"', "", cleaned)
        cleaned = re.sub(r"\son\w+='[^']*'", "", cleaned)
        cleaned = re.sub(
            r'href\s*=\s*([\'"])\s*javascript:[^\'"]*\1',
            'href="#"',
            cleaned,
            flags=re.IGNORECASE,
        )
        return cleaned

    def linkify(value: str, callbacks=None):
        return value

    bleach_module.clean = clean
    bleach_module.linkify = linkify
    monkeypatch.setitem(sys.modules, "bleach", bleach_module)

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


def _extract_detail_payload(body: dict[str, Any]) -> dict[str, Any]:
    data = body["data"]
    if isinstance(data, dict) and isinstance(data.get("message"), dict):
        return data["message"]
    if isinstance(data, dict):
        return data
    raise AssertionError("邮件详情响应 data 不是对象")


def _first_present(payload: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        current: Any = payload
        found = True
        for key in path:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                found = False
                break
        if found:
            return current
    raise AssertionError(f"未找到字段路径：{paths}")


def _normalize_addresses(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value.strip().lower()}
    if isinstance(value, dict):
        email = value.get("email") or value.get("address") or value.get("value")
        return {str(email).strip().lower()} if email else set()
    if isinstance(value, list):
        items: set[str] = set()
        for item in value:
            items.update(_normalize_addresses(item))
        return items
    return {str(value).strip().lower()}


def _contains_all(haystack: Any, parts: list[str]) -> bool:
    text = "" if haystack is None else str(haystack).lower()
    return all(part.lower() in text for part in parts)


def test_message_detail_requires_login(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)

    response = client.get("/api/folders/INBOX/messages/101")

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"] is not None
    assert body["request_id"]


def test_message_detail_returns_headers_and_sanitized_html(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    raw_bytes = _make_message_bytes(
        subject="测试邮件详情",
        sender_name="发件人姓名",
        sender_email="sender@example.com",
        to_emails=["to@example.com"],
        cc_emails=["cc@example.com"],
        date_value=datetime(2026, 5, 7, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="纯文本备用正文",
        html_body=(
            '<div>'
            '<script>alert("xss")</script>'
            '<img src="x" onerror="alert(1)">'
            '<a href="javascript:alert(2)">危险链接</a>'
            "<p>安全正文</p>"
            "</div>"
        ),
        attachments=[("report.pdf", "application/pdf", b"%PDF-1.4 fake pdf")],
    )
    FakeImapAdapter.seed_message("INBOX", "101", raw_bytes)

    response = client.get("/api/folders/INBOX/messages/101")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["request_id"]

    payload = _extract_detail_payload(body)
    sender_value = _first_present(payload, ("sender_name",), ("from",), ("sender",))
    sender_email = _normalize_addresses(sender_value)
    to_emails = _normalize_addresses(_first_present(payload, ("to_emails",), ("to",), ("recipients", "to")))
    cc_emails = _normalize_addresses(_first_present(payload, ("cc_emails",), ("cc",)))
    subject = _first_present(payload, ("subject",))
    date_value = _first_present(payload, ("sent_at",), ("received_at",), ("date",), ("headers", "date"))

    assert _contains_all(sender_value, ["发件人姓名"])
    assert sender_email == {"sender@example.com"}
    assert to_emails == {"to@example.com"}
    assert cc_emails == {"cc@example.com"}
    assert subject == "测试邮件详情"
    assert date_value

    html_body = _first_present(payload, ("html_body",), ("body", "html"), ("body_html",))
    text_body = _first_present(payload, ("text_body",), ("body", "text"), ("body_text",))

    assert _contains_all(html_body, ["安全正文"])
    assert "script" not in str(html_body).lower()
    assert "onerror" not in str(html_body).lower()
    assert "javascript:" not in str(html_body).lower()
    assert "危险链接" in str(html_body)
    assert str(text_body).strip() == "纯文本备用正文"


def test_message_detail_preserves_rich_text_html(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    raw_bytes = _make_message_bytes(
        subject="富文本邮件",
        sender_name="发件人姓名",
        sender_email="sender@example.com",
        to_emails=["to@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="纯文本备用正文",
        html_body=(
            '<style>.notice{color:#2ecc71;font-size:20px;}'
            '.unsafe{background-image:url(https://evil.example/track.png);behavior:url(xss.htc);}</style>'
            '<p><span style="color:#e74c3c;background-color:#fff3cd;font-family:Arial;font-size:18px;">红色正文</span></p>'
            '<p class="notice unsafe">样式块正文</p>'
            '<table><tbody><tr><td style="border:1px solid #d8dee9;">单元格</td></tr></tbody></table>'
            '<img src="data:image/png;base64,aGVsbG8=" alt="内联图片">'
            '<script>alert("xss")</script>'
        ),
    )
    FakeImapAdapter.seed_message("INBOX", "102", raw_bytes)

    response = client.get("/api/folders/INBOX/messages/102")

    assert response.status_code == 200
    payload = _extract_detail_payload(response.json())
    html_body = _first_present(payload, ("html_body",), ("body", "html"), ("body_html",))

    assert _contains_all(html_body, ["红色正文", "样式块正文", "table", "单元格", "data:image/png"])
    assert 'class="notice unsafe"' in str(html_body)
    assert "color:#2ecc71" in str(html_body)
    assert "font-size:20px" in str(html_body)
    assert "color:#e74c3c" in str(html_body)
    assert "background-color:#fff3cd" in str(html_body)
    assert "font-family:Arial" in str(html_body)
    assert "script" not in str(html_body).lower()
    assert "behavior" not in str(html_body).lower()
    assert "evil.example" not in str(html_body).lower()


def test_message_detail_preserves_font_tags_in_html_body(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    raw_bytes = _make_message_bytes(
        subject="字体标签邮件",
        sender_name="Font Sender",
        sender_email="font@example.com",
        to_emails=["reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 9, 45, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="字体标签纯文本备用正文",
        html_body='<div><font color="red">红色字体正文</font></div>',
    )
    FakeImapAdapter.seed_message("INBOX", "102", raw_bytes)

    response = client.get("/api/folders/INBOX/messages/102")

    assert response.status_code == 200
    payload = _extract_detail_payload(response.json())
    html_body = _first_present(payload, ("html_body",), ("body", "html"), ("body_html",))

    assert "<font color=\"red\">" in str(html_body)
    assert "&lt;font" not in str(html_body)
    assert "红色字体正文" in str(html_body)


def test_message_detail_returns_plain_text_body_when_html_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    raw_bytes = _make_message_bytes(
        subject="纯文本邮件",
        sender_name="Plain Sender",
        sender_email="plain@example.com",
        to_emails=["reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="这是一封纯文本正文",
        html_body=None,
    )
    FakeImapAdapter.seed_message("INBOX", "102", raw_bytes)

    response = client.get("/api/folders/INBOX/messages/102")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True

    payload = _extract_detail_payload(body)
    text_body = _first_present(payload, ("text_body",), ("body", "text"), ("body_text",))
    html_body = payload.get("html_body") or payload.get("body_html") or payload.get("body", {}).get("html")

    assert str(text_body).strip() == "这是一封纯文本正文"
    if html_body not in {None, ""}:
        assert "这是一封纯文本正文" in str(html_body)


def test_message_detail_marks_message_read_or_reports_read_true(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    raw_bytes = _make_message_bytes(
        subject="待读邮件",
        sender_name="Unread Sender",
        sender_email="unread@example.com",
        to_emails=["reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="打开后应标记已读",
        html_body="<p>打开后应标记已读</p>",
    )
    FakeImapAdapter.seed_message("INBOX", "103", raw_bytes)

    response = client.get("/api/folders/INBOX/messages/103")

    assert response.status_code == 200
    body = response.json()
    payload = _extract_detail_payload(body)
    read_value = _first_present(payload, ("read",), ("is_read",), ("message", "is_read"))

    assert read_value in {True, "true", "True", 1} or bool(FakeImapAdapter.store_calls)
    assert FakeImapAdapter.store_calls or read_value in {True, "true", "True", 1}


def test_message_detail_exposes_attachment_metadata_without_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    raw_bytes = _make_message_bytes(
        subject="带附件邮件",
        sender_name="Attach Sender",
        sender_email="attach@example.com",
        to_emails=["reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="附件应只展示元数据",
        html_body="<p>附件应只展示元数据</p>",
        attachments=[
            ("contract.pdf", "application/pdf", b"%PDF-1.4 fake attachment"),
            ("notes.txt", "text/plain", b"plain attachment"),
        ],
    )
    FakeImapAdapter.seed_message("INBOX", "104", raw_bytes)

    response = client.get("/api/folders/INBOX/messages/104")

    assert response.status_code == 200
    body = response.json()
    payload = _extract_detail_payload(body)
    attachments = _first_present(payload, ("attachments",), ("message_attachments",))

    assert isinstance(attachments, list)
    assert len(attachments) == 2

    first_attachment = attachments[0]
    assert isinstance(first_attachment, dict)
    assert first_attachment.get("filename") == "contract.pdf"
    assert first_attachment.get("content_type") == "application/pdf"
    assert first_attachment.get("size_bytes") == len(b"%PDF-1.4 fake attachment")
    assert "content" not in first_attachment
    assert "data" not in first_attachment
    assert "raw" not in first_attachment
