from __future__ import annotations

import hashlib
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
from urllib.parse import quote
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
    mailboxes: dict[tuple[str, str, str], FakeMailboxMessage] = {}
    login_calls: list[tuple[str, str]] = []
    selected_folders: list[tuple[str, str]] = []
    last_instance: "FakeImapAdapter | None" = None

    def __init__(self, settings) -> None:
        self.settings = settings
        self.connected = False
        self.logged_in = False
        self.logged_out = False
        self.selected_folder: str | None = None
        self.account_email: str | None = None
        FakeImapAdapter.last_instance = self

    @classmethod
    def reset(cls) -> None:
        cls.mailboxes = {}
        cls.login_calls = []
        cls.selected_folders = []
        cls.last_instance = None

    @classmethod
    def seed_message(cls, account: str, folder: str, uid: str, raw_bytes: bytes) -> None:
        cls.mailboxes[(account.lower(), folder, uid)] = FakeMailboxMessage(folder=folder, uid=uid, raw_bytes=raw_bytes)

    def connect(self):
        self.connected = True
        return self

    def login(self):
        FakeImapAdapter.login_calls.append((self.settings.username, self.settings.password))
        if getattr(self.settings, "password", None) == "wrong-password":
            raise MailAdapterError("IMAP 登录失败", operation="login")
        self.logged_in = True
        self.account_email = str(getattr(self.settings, "username", "")).lower()
        return self

    def logout(self):
        self.logged_out = True
        return self

    def select_folder(self, folder: str):
        if self.account_email is None:
            raise MailAdapterError("未登录 IMAP", operation="select_folder")
        self.selected_folder = folder
        FakeImapAdapter.selected_folders.append((self.account_email, folder))
        return "OK", [b"1"]

    def fetch_message_bytes(self, uid: str | bytes) -> bytes:
        uid_str = uid.decode("utf-8") if isinstance(uid, bytes) else str(uid)
        if self.account_email is None or self.selected_folder is None:
            raise MailAdapterError("未选择文件夹", operation="fetch_message_bytes")
        message = self.mailboxes.get((self.account_email, self.selected_folder, uid_str))
        if message is None:
            raise MailAdapterError("IMAP 邮件内容为空", operation="fetch_message_bytes")
        return message.raw_bytes

    def fetch(self, uid: str | bytes, query: str):
        payload = self.fetch_message_bytes(uid)
        return "OK", [(b"RFC822", payload)]

    def uid_fetch_message_bytes(self, uid: str | bytes) -> bytes:
        return self.fetch_message_bytes(uid)

    def mark_seen(self, uid: str | bytes):
        return self

    def status(self, folder: str):
        if self.account_email is None:
            return {"UNSEEN": 0, "MESSAGES": 0, "UIDVALIDITY": 1}
        total = 0
        for (account, candidate_folder, _uid), _message in self.mailboxes.items():
            if account == self.account_email and candidate_folder == folder:
                total += 1
        return {"UNSEEN": 0, "MESSAGES": total, "UIDVALIDITY": 1}


def make_settings(*, login_fail_limit: int = 5) -> FakeSettings:
    return FakeSettings(login_fail_limit=login_fail_limit)


def _make_message_bytes(
    *,
    subject: str,
    sender_name: str,
    sender_email: str,
    to_emails: list[str],
    cc_emails: list[str],
    date_value: datetime,
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


def build_app(monkeypatch: pytest.MonkeyPatch, *, login_fail_limit: int = 5):
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

    multipart_module = ModuleType("multipart")
    multipart_module.__version__ = "0.0.20"
    multipart_submodule = ModuleType("multipart.multipart")

    def parse_options_header(value):
        return value, {}

    multipart_submodule.parse_options_header = parse_options_header
    multipart_module.multipart = multipart_submodule
    monkeypatch.setitem(sys.modules, "multipart", multipart_module)
    monkeypatch.setitem(sys.modules, "multipart.multipart", multipart_submodule)

    python_multipart_module = ModuleType("python_multipart")
    python_multipart_module.__version__ = "0.0.20"
    monkeypatch.setitem(sys.modules, "python_multipart", python_multipart_module)

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
    return main_module


def build_client(monkeypatch: pytest.MonkeyPatch, *, login_fail_limit: int = 5) -> TestClient:
    main_module = build_app(monkeypatch, login_fail_limit=login_fail_limit)
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


def _extract_attachment_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    attachments = payload.get("attachments") or payload.get("message_attachments")
    if not isinstance(attachments, list):
        raise AssertionError(f"未找到附件列表: {payload!r}")
    return [item for item in attachments if isinstance(item, dict)]


def _attachment_candidates(index: int, attachment: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in ("attachment_id", "id", "part_id", "part", "index"):
        value = attachment.get(key)
        if value is not None:
            candidates.append(str(value))
    candidates.extend([str(index), str(index + 1)])
    filename = attachment.get("filename")
    if filename:
        candidates.append(str(filename))
    return list(dict.fromkeys(candidates))


def _json_error(response) -> dict[str, Any]:
    body = response.json()
    assert isinstance(body, dict)
    assert body.get("success") is False
    assert body.get("error") is not None
    assert "request_id" in body
    return body


def _download_attachment(
    client: TestClient,
    folder: str,
    uid: str,
    attachment: dict[str, Any],
    index: int,
):
    for candidate in _attachment_candidates(index, attachment):
        response = client.get(
            f"/api/folders/{folder}/messages/{uid}/attachments/{quote(candidate, safe='')}"
        )
        if response.status_code == 200:
            return candidate, response
    raise AssertionError(f"附件下载未命中可用 attachment_id，候选值：{_attachment_candidates(index, attachment)}")


def test_attachment_download_requires_login(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)

    response = client.get("/api/folders/INBOX/messages/101/attachments/1")

    assert response.status_code == 401
    _json_error(response)


def test_attachment_download_returns_bytes_and_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    main_module = build_app(monkeypatch)
    client = TestClient(main_module.app, raise_server_exceptions=False)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    attachment_bytes = b"%PDF-1.4 fake pdf attachment\n"
    raw_bytes = _make_message_bytes(
        subject="带附件邮件",
        sender_name="Attach Sender",
        sender_email="attach@example.com",
        to_emails=["reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="附件应只展示元数据",
        html_body="<p>附件应只展示元数据</p>",
        attachments=[("report.pdf", "application/pdf", attachment_bytes)],
    )
    FakeImapAdapter.seed_message("user@example.com", "INBOX", "101", raw_bytes)

    detail_response = client.get("/api/folders/INBOX/messages/101")
    assert detail_response.status_code == 200
    payload = _extract_detail_payload(detail_response.json())
    attachments = _extract_attachment_list(payload)
    assert len(attachments) == 1

    candidate, response = _download_attachment(client, "INBOX", "101", attachments[0], 0)

    assert response.status_code == 200
    assert response.content == attachment_bytes
    assert hashlib.sha256(response.content).hexdigest() == hashlib.sha256(attachment_bytes).hexdigest()
    assert response.headers["content-type"].startswith("application/pdf")
    content_disposition = response.headers.get("content-disposition", "")
    assert "attachment" in content_disposition.lower()
    assert "report.pdf" in content_disposition
    assert candidate


def test_attachment_download_exposes_word_attachment_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    main_module = build_app(monkeypatch)
    client = TestClient(main_module.app, raise_server_exceptions=False)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    attachment_bytes = b"PK\x03\x04 fake docx attachment\n"
    raw_bytes = _make_message_bytes(
        subject="Word 附件邮件",
        sender_name="Attach Sender",
        sender_email="attach@example.com",
        to_emails=["reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 12, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="Word 附件应只展示元数据",
        html_body="<p>Word 附件应只展示元数据</p>",
        attachments=[
            (
                "proposal.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                attachment_bytes,
            ),
        ],
    )
    FakeImapAdapter.seed_message("user@example.com", "INBOX", "102", raw_bytes)

    detail_response = client.get("/api/folders/INBOX/messages/102")
    assert detail_response.status_code == 200
    payload = _extract_detail_payload(detail_response.json())
    attachments = _extract_attachment_list(payload)

    assert len(attachments) == 1
    attachment = attachments[0]
    assert attachment.get("filename") == "proposal.docx"
    assert attachment.get("content_type") == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert attachment.get("size_bytes") == len(attachment_bytes)
    assert "content" not in attachment
    assert "data" not in attachment


def test_attachment_download_invalid_attachment_id_returns_not_found_or_error(monkeypatch: pytest.MonkeyPatch) -> None:
    main_module = build_app(monkeypatch)
    client = TestClient(main_module.app, raise_server_exceptions=False)
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
        attachments=[("report.pdf", "application/pdf", b"%PDF-1.4 fake pdf attachment\n")],
    )
    FakeImapAdapter.seed_message("user@example.com", "INBOX", "101", raw_bytes)

    response = client.get("/api/folders/INBOX/messages/101/attachments/not-exist")

    assert response.status_code in {403, 404}
    if response.headers.get("content-type", "").startswith("application/json"):
        _json_error(response)


def test_attachment_download_denies_cross_account_access(monkeypatch: pytest.MonkeyPatch) -> None:
    main_module = build_app(monkeypatch)
    client_a = TestClient(main_module.app, raise_server_exceptions=False)
    client_b = TestClient(main_module.app, raise_server_exceptions=False)

    login_a = login(client_a, "alice@example.com", "correct-password")
    login_b = login(client_b, "bob@example.com", "correct-password")
    assert login_a.status_code == 200
    assert login_b.status_code == 200

    alice_raw_bytes = _make_message_bytes(
        subject="Alice 邮件",
        sender_name="Alice Sender",
        sender_email="alice@example.com",
        to_emails=["alice-reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="A 账号自己的邮件",
        html_body="<p>A 账号自己的邮件</p>",
        attachments=[("alice.txt", "text/plain", b"alice attachment bytes")],
    )
    attachment_bytes = b"cross-account attachment bytes"
    raw_bytes = _make_message_bytes(
        subject="跨账号附件",
        sender_name="Bob Sender",
        sender_email="bob@example.com",
        to_emails=["bob-reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="仅供 B 账号访问",
        html_body="<p>仅供 B 账号访问</p>",
        attachments=[("private.txt", "text/plain", attachment_bytes)],
    )
    FakeImapAdapter.seed_message("alice@example.com", "INBOX", "201", alice_raw_bytes)
    FakeImapAdapter.seed_message("bob@example.com", "INBOX", "201", raw_bytes)

    alice_detail = client_a.get("/api/folders/INBOX/messages/201")
    assert alice_detail.status_code == 200
    detail_response = client_b.get("/api/folders/INBOX/messages/201")
    assert detail_response.status_code == 200
    payload = _extract_detail_payload(detail_response.json())
    attachments = _extract_attachment_list(payload)
    assert len(attachments) == 1

    response = client_a.get("/api/folders/INBOX/messages/201/attachments/0")

    assert response.status_code in {403, 404}
    if response.headers.get("content-type", "").startswith("application/json"):
        _json_error(response)
