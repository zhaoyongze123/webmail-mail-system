from __future__ import annotations

import hashlib
import importlib
import io
import re
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.parser import BytesHeaderParser
from email.utils import format_datetime
from types import ModuleType
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import fakeredis
import pydantic.networks as pydantic_networks
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.mail_adapters import MailAdapterError


class FakeSettings:
    def __init__(
        self,
        *,
        login_fail_limit: int = 5,
        attachment_preview_cache_dir: str = "/tmp/webmail-preview-cache-test",
        attachment_preview_cache_ttl_seconds: int = 3600,
        attachment_preview_cache_max_mb: int = 32,
        attachment_preview_housekeeping_interval_seconds: int = 1,
        attachment_preview_processing_timeout_seconds: int = 30,
    ) -> None:
        self.app_env = "test"
        self.app_name = "webmail-mvp"
        self.app_secret_key = "test-secret"
        self.database_url = "sqlite+pysqlite:///:memory:"
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
        self.attachment_preview_cache_dir = attachment_preview_cache_dir
        self.attachment_preview_cache_ttl_seconds = attachment_preview_cache_ttl_seconds
        self.attachment_preview_cache_max_mb = attachment_preview_cache_max_mb
        self.attachment_preview_housekeeping_interval_seconds = attachment_preview_housekeeping_interval_seconds
        self.attachment_preview_processing_timeout_seconds = attachment_preview_processing_timeout_seconds

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
    fetch_calls: list[tuple[str, str, str]] = []
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
        cls.fetch_calls = []
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

    def list_folders(self) -> list[str]:
        folders = {"INBOX", ".Sent", ".Drafts", ".Junk", ".Trash", ".Archive"}
        folders.update(folder for (_account, folder, _uid) in self.mailboxes)
        return [f'(\\HasNoChildren) "/" "{folder}"' for folder in sorted(folders)]

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
        FakeImapAdapter.fetch_calls.append((self.account_email, self.selected_folder, uid_str))
        return self._raw_message_bytes(uid_str)

    def _raw_message_bytes(self, uid: str) -> bytes:
        message = self.mailboxes.get((self.account_email, self.selected_folder, uid))
        if message is None:
            raise MailAdapterError("IMAP 邮件内容为空", operation="fetch_message_bytes")
        return message.raw_bytes

    def fetch(self, uid: str | bytes, query: str):
        payload = self.fetch_message_bytes(uid)
        return "OK", [(b"RFC822", payload)]

    def search_uids(self, criteria: str):
        if self.selected_folder is None:
            return []
        return [
            uid
            for (account, folder, uid), _message in self.mailboxes.items()
            if account == self.account_email and folder == self.selected_folder
        ]

    def uid_fetch_message_bytes(self, uid: str | bytes) -> bytes:
        return self.fetch_message_bytes(uid)

    def uid_fetch_headers(self, uid: str | bytes) -> bytes:
        uid_str = uid.decode("utf-8") if isinstance(uid, bytes) else str(uid)
        raw = self._raw_message_bytes(uid_str)
        separator = b"\r\n\r\n" if b"\r\n\r\n" in raw else b"\n\n"
        return raw.split(separator, 1)[0] + separator

    def uid_fetch_bodystructure(self, uid: str | bytes) -> bytes:
        uid_str = uid.decode("utf-8") if isinstance(uid, bytes) else str(uid)
        message = message_from_bytes(self._raw_message_bytes(uid_str), policy=policy.default)
        leaves = []
        for part in message.walk():
            if part.is_multipart():
                continue
            content_type = part.get_content_type()
            maintype, subtype = content_type.split("/", 1)
            filename = part.get_filename()
            params = []
            charset = part.get_content_charset()
            if charset:
                params.extend(['"CHARSET"', f'"{charset}"'])
            if filename:
                params.extend(['"NAME"', f'"{filename}"'])
            params_text = f"({' '.join(params)})" if params else "NIL"
            payload = part.get_payload(decode=True) or b""
            encoding = str(part.get("Content-Transfer-Encoding") or "7BIT").upper()
            disposition = part.get_content_disposition()
            disposition_text = "NIL"
            if disposition:
                disp_params = []
                if filename:
                    disp_params.extend(['"FILENAME"', f'"{filename}"'])
                disposition_text = f'("{disposition.upper()}" ({ " ".join(disp_params) }))' if disp_params else f'("{disposition.upper()}" NIL)'
            leaves.append(
                f'("{maintype.upper()}" "{subtype.upper()}" {params_text} NIL NIL "{encoding}" {len(payload)} NIL {disposition_text} NIL)'
            )
        structure = f"({' '.join(leaves)} \"MIXED\")" if len(leaves) > 1 else f"({leaves[0]})"
        return f'1 (UID {uid} BODYSTRUCTURE {structure})'.encode()

    def uid_fetch_body_section(self, uid: str | bytes, section: str) -> bytes:
        uid_str = uid.decode("utf-8") if isinstance(uid, bytes) else str(uid)
        full = message_from_bytes(self._raw_message_bytes(uid_str), policy=policy.default)
        leaf_parts = [part for part in full.walk() if not part.is_multipart()]
        try:
            target = leaf_parts[max(int(section.split(".")[-1]) - 1, 0)]
        except Exception:
            return self._raw_message_bytes(uid_str)
        payload = target.get_payload(decode=False)
        if isinstance(payload, str):
            return payload.encode("utf-8")
        if isinstance(payload, bytes):
            return payload
        return b""

    def mark_seen(self, uid: str | bytes):
        return self

    def status(self, folder: str, items: str | None = None):
        if self.account_email is None:
            return {"UNSEEN": 0, "MESSAGES": 0, "UIDVALIDITY": 1}
        total = 0
        for (account, candidate_folder, _uid), _message in self.mailboxes.items():
            if account == self.account_email and candidate_folder == folder:
                total += 1
        return {"UNSEEN": 0, "MESSAGES": total, "UIDVALIDITY": 1}

    def copy_message(self, uid: str | bytes, target_folder: str):
        return self

    def store_flags(self, uid: str | bytes, command: str, flags: str):
        return self

    def expunge(self):
        return self


class InlineExecutor:
    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)
        return None


def make_settings(
    *,
    login_fail_limit: int = 5,
    attachment_preview_cache_dir: str = "/tmp/webmail-preview-cache-test",
    attachment_preview_cache_ttl_seconds: int = 3600,
    attachment_preview_cache_max_mb: int = 32,
    attachment_preview_housekeeping_interval_seconds: int = 1,
    attachment_preview_processing_timeout_seconds: int = 30,
) -> FakeSettings:
    return FakeSettings(
        login_fail_limit=login_fail_limit,
        attachment_preview_cache_dir=attachment_preview_cache_dir,
        attachment_preview_cache_ttl_seconds=attachment_preview_cache_ttl_seconds,
        attachment_preview_cache_max_mb=attachment_preview_cache_max_mb,
        attachment_preview_housekeeping_interval_seconds=attachment_preview_housekeeping_interval_seconds,
        attachment_preview_processing_timeout_seconds=attachment_preview_processing_timeout_seconds,
    )


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


def build_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    login_fail_limit: int = 5,
    attachment_preview_cache_dir: str = "/tmp/webmail-preview-cache-test",
    attachment_preview_cache_ttl_seconds: int = 3600,
    attachment_preview_cache_max_mb: int = 32,
    attachment_preview_housekeeping_interval_seconds: int = 1,
    attachment_preview_processing_timeout_seconds: int = 30,
):
    FakeImapAdapter.reset()
    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    settings = make_settings(
        login_fail_limit=login_fail_limit,
        attachment_preview_cache_dir=attachment_preview_cache_dir,
        attachment_preview_cache_ttl_seconds=attachment_preview_cache_ttl_seconds,
        attachment_preview_cache_max_mb=attachment_preview_cache_max_mb,
        attachment_preview_housekeeping_interval_seconds=attachment_preview_housekeeping_interval_seconds,
        attachment_preview_processing_timeout_seconds=attachment_preview_processing_timeout_seconds,
    )

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

    for module_name in [
        "app.config",
        "app.cache",
        "app.redis_client",
        "app.db",
        "app.models",
        "app.observability",
        "app.security",
        "app.mail_directory",
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

    old_mailbox_module = sys.modules.get("app.mailbox")
    old_executor = getattr(old_mailbox_module, "MAILBOX_BACKGROUND_EXECUTOR", None) if old_mailbox_module else None
    if old_executor is not None:
        old_executor.shutdown(wait=True, cancel_futures=True)

    models_module = importlib.import_module("app.models")
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(db_module, "get_engine", lambda database_url=None: engine)
    models_module.Base.metadata.create_all(engine)

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


def _preview_attachment(
    client: TestClient,
    folder: str,
    uid: str,
    attachment: dict[str, Any],
    index: int,
):
    for candidate in _attachment_candidates(index, attachment):
        response = client.get(
            f"/api/folders/{folder}/messages/{uid}/attachments/{quote(candidate, safe='')}/preview"
        )
        if response.status_code == 200:
            return candidate, response
    raise AssertionError(f"附件预览未命中可用 attachment_id，候选值：{_attachment_candidates(index, attachment)}")


def _preview_attachment_thumbnail(
    client: TestClient,
    folder: str,
    uid: str,
    attachment: dict[str, Any],
    index: int,
):
    for candidate in _attachment_candidates(index, attachment):
        response = client.get(
            f"/api/folders/{folder}/messages/{uid}/attachments/{quote(candidate, safe='')}/preview-thumbnail"
        )
        if response.status_code == 200:
            return candidate, response
    raise AssertionError(f"附件缩略图未命中可用 attachment_id，候选值：{_attachment_candidates(index, attachment)}")


def _make_docx_bytes(*paragraphs: str) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>"
        for paragraph in paragraphs
    )
    document_xml = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
        f"<w:body>{body}</w:body>"
        "</w:document>"
    ).encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def _make_pdf_bytes(*lines: str) -> bytes:
    fitz_module = pytest.importorskip("fitz")
    document = fitz_module.open()
    try:
        page = document.new_page()
        cursor_y = 72
        for line in lines or ("测试 PDF 预览",):
            page.insert_text((72, cursor_y), line, fontsize=14)
            cursor_y += 24
        return document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()


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


def test_attachment_preview_returns_pdf_bytes_after_section_transfer_decode(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    main_module = build_app(monkeypatch, attachment_preview_cache_dir=str(tmp_path / "preview-cache"))
    mailbox_module = importlib.import_module("app.mailbox")
    monkeypatch.setattr(mailbox_module, "MAILBOX_BACKGROUND_EXECUTOR", InlineExecutor())
    client = TestClient(main_module.app, raise_server_exceptions=False)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    attachment_bytes = _make_pdf_bytes("PDF 预览内容", "第二行说明")
    raw_bytes = _make_message_bytes(
        subject="PDF 预览邮件",
        sender_name="Attach Sender",
        sender_email="attach@example.com",
        to_emails=["reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 12, 5, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="请查看 pdf 附件",
        html_body="<p>请查看 pdf 附件</p>",
        attachments=[("preview.pdf", "application/pdf", attachment_bytes)],
    )
    FakeImapAdapter.seed_message("user@example.com", "INBOX", "104", raw_bytes)

    detail_response = client.get("/api/folders/INBOX/messages/104")
    assert detail_response.status_code == 200
    payload = _extract_detail_payload(detail_response.json())
    attachments = _extract_attachment_list(payload)
    assert len(attachments) == 1

    candidate, preview_response = _preview_attachment(client, "INBOX", "104", attachments[0], 0)

    assert preview_response.status_code == 200
    assert preview_response.headers["content-type"].startswith("application/pdf")
    assert preview_response.content == attachment_bytes
    assert candidate


def test_attachment_preview_thumbnail_returns_png_and_status_for_pdf(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    main_module = build_app(monkeypatch, attachment_preview_cache_dir=str(tmp_path / "preview-cache"))
    mailbox_module = importlib.import_module("app.mailbox")
    monkeypatch.setattr(mailbox_module, "MAILBOX_BACKGROUND_EXECUTOR", InlineExecutor())
    client = TestClient(main_module.app, raise_server_exceptions=False)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    attachment_bytes = _make_pdf_bytes("缩略图首页", "用于校验 PNG 返回")
    raw_bytes = _make_message_bytes(
        subject="PDF 缩略图",
        sender_name="Attach Sender",
        sender_email="attach@example.com",
        to_emails=["reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 12, 6, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="请查看 PDF 缩略图",
        html_body="<p>请查看 PDF 缩略图</p>",
        attachments=[("thumbnail.pdf", "application/pdf", attachment_bytes)],
    )
    FakeImapAdapter.seed_message("user@example.com", "INBOX", "105", raw_bytes)

    detail_response = client.get("/api/folders/INBOX/messages/105")
    assert detail_response.status_code == 200
    attachments = _extract_attachment_list(_extract_detail_payload(detail_response.json()))
    attachment_id, thumbnail_response = _preview_attachment_thumbnail(client, "INBOX", "105", attachments[0], 0)

    assert thumbnail_response.status_code == 200
    assert thumbnail_response.headers["content-type"].startswith("image/png")
    assert len(thumbnail_response.content) > 0

    status_response = client.get(
        f"/api/folders/INBOX/messages/105/attachments/{quote(attachment_id, safe='')}/preview/status"
    )
    assert status_response.status_code == 200
    payload = status_response.json()["data"]
    assert payload["preview_kind"] == "pdf"
    assert payload["status"] == "ready"
    assert payload["thumbnail_ready"] is True
    assert payload["thumbnail_content_type"] == "image/png"


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


def test_attachment_preview_returns_docx_html_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    main_module = build_app(monkeypatch)
    client = TestClient(main_module.app, raise_server_exceptions=False)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    attachment_bytes = _make_docx_bytes("聚类算法实验报告", "第二段正文")
    raw_bytes = _make_message_bytes(
        subject="Word 附件预览",
        sender_name="Attach Sender",
        sender_email="attach@example.com",
        to_emails=["reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 12, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="请查看 docx 附件",
        html_body="<p>请查看 docx 附件</p>",
        attachments=[
            (
                "proposal.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                attachment_bytes,
            ),
        ],
    )
    FakeImapAdapter.seed_message("user@example.com", "INBOX", "103", raw_bytes)

    detail_response = client.get("/api/folders/INBOX/messages/103")
    assert detail_response.status_code == 200
    payload = _extract_detail_payload(detail_response.json())
    attachments = _extract_attachment_list(payload)
    assert len(attachments) == 1

    candidate, response = _preview_attachment(client, "INBOX", "103", attachments[0], 0)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    content_disposition = response.headers.get("content-disposition", "")
    assert "inline" in content_disposition.lower()
    assert "proposal.docx" in content_disposition
    text = response.text
    assert "proposal.docx" in text
    assert "聚类算法实验报告" in text
    assert "第二段正文" in text
    assert candidate


def test_attachment_preview_thumbnail_returns_svg_and_status_for_docx(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    main_module = build_app(monkeypatch, attachment_preview_cache_dir=str(tmp_path / "preview-cache"))
    mailbox_module = importlib.import_module("app.mailbox")
    monkeypatch.setattr(mailbox_module, "MAILBOX_BACKGROUND_EXECUTOR", InlineExecutor())
    client = TestClient(main_module.app, raise_server_exceptions=False)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    attachment_bytes = _make_docx_bytes("第一行摘要", "第二行内容", "第三行内容")
    raw_bytes = _make_message_bytes(
        subject="DOCX 缩略图",
        sender_name="Attach Sender",
        sender_email="attach@example.com",
        to_emails=["reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 12, 11, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="请查看 docx 缩略图",
        html_body="<p>请查看 docx 缩略图</p>",
        attachments=[
            (
                "proposal.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                attachment_bytes,
            ),
        ],
    )
    FakeImapAdapter.seed_message("user@example.com", "INBOX", "107", raw_bytes)

    detail_response = client.get("/api/folders/INBOX/messages/107")
    assert detail_response.status_code == 200
    attachments = _extract_attachment_list(_extract_detail_payload(detail_response.json()))
    attachment_id, thumbnail_response = _preview_attachment_thumbnail(client, "INBOX", "107", attachments[0], 0)

    assert thumbnail_response.status_code == 200
    assert thumbnail_response.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in thumbnail_response.content
    assert b"DOCX" in thumbnail_response.content

    status_response = client.get(
        f"/api/folders/INBOX/messages/107/attachments/{quote(attachment_id, safe='')}/preview/status"
    )
    assert status_response.status_code == 200
    payload = status_response.json()["data"]
    assert payload["preview_kind"] == "text"
    assert payload["status"] == "ready"
    assert payload["thumbnail_ready"] is True
    assert payload["thumbnail_content_type"] == "image/svg+xml"


def test_attachment_download_reuses_cached_attachment_after_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    main_module = build_app(monkeypatch)
    client = TestClient(main_module.app, raise_server_exceptions=False)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    attachment_bytes = b"%PDF-1.4 cached attachment\n"
    raw_bytes = _make_message_bytes(
        subject="缓存附件邮件",
        sender_name="Attach Sender",
        sender_email="attach@example.com",
        to_emails=["reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 12, 20, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="先开详情再下载附件",
        html_body="<p>先开详情再下载附件</p>",
        attachments=[("cached.pdf", "application/pdf", attachment_bytes)],
    )
    FakeImapAdapter.seed_message("user@example.com", "INBOX", "106", raw_bytes)

    initial_fetch_count = FakeImapAdapter.fetch_calls.count(("user@example.com", "INBOX", "106"))
    detail_response = client.get("/api/folders/INBOX/messages/106")
    assert detail_response.status_code == 200
    after_detail_fetch_count = FakeImapAdapter.fetch_calls.count(("user@example.com", "INBOX", "106"))
    payload = _extract_detail_payload(detail_response.json())
    attachments = _extract_attachment_list(payload)
    assert len(attachments) == 1

    candidate, download_response = _download_attachment(client, "INBOX", "106", attachments[0], 0)
    after_download_fetch_count = FakeImapAdapter.fetch_calls.count(("user@example.com", "INBOX", "106"))

    assert download_response.status_code == 200
    assert download_response.content == attachment_bytes
    assert after_detail_fetch_count >= initial_fetch_count
    assert after_download_fetch_count <= after_detail_fetch_count
    assert candidate


def test_attachment_preview_reuses_persisted_cache_without_imap(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    main_module = build_app(monkeypatch, attachment_preview_cache_dir=str(tmp_path / "preview-cache"))
    client = TestClient(main_module.app, raise_server_exceptions=False)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    attachment_bytes = _make_docx_bytes("第一次生成缓存", "第二段正文")
    raw_bytes = _make_message_bytes(
        subject="缓存预览附件",
        sender_name="Attach Sender",
        sender_email="attach@example.com",
        to_emails=["reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 12, 40, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="docx 预览缓存",
        html_body="<p>docx 预览缓存</p>",
        attachments=[
            (
                "cached.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                attachment_bytes,
            ),
        ],
    )
    FakeImapAdapter.seed_message("user@example.com", "INBOX", "120", raw_bytes)

    detail_response = client.get("/api/folders/INBOX/messages/120")
    assert detail_response.status_code == 200
    attachments = _extract_attachment_list(_extract_detail_payload(detail_response.json()))
    candidate, first_preview = _preview_attachment(client, "INBOX", "120", attachments[0], 0)
    assert first_preview.status_code == 200

    FakeImapAdapter.mailboxes.pop(("user@example.com", "INBOX", "120"), None)
    second_preview = client.get(f"/api/folders/INBOX/messages/120/attachments/{quote(candidate, safe='')}/preview")
    assert second_preview.status_code == 200
    assert second_preview.content == first_preview.content


def test_attachment_preview_rebuilds_missing_pdf_thumbnail_from_persisted_cache(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    cache_dir = tmp_path / "preview-cache"
    main_module = build_app(monkeypatch, attachment_preview_cache_dir=str(cache_dir))
    mailbox_module = importlib.import_module("app.mailbox")
    monkeypatch.setattr(mailbox_module, "MAILBOX_BACKGROUND_EXECUTOR", InlineExecutor())
    client = TestClient(main_module.app, raise_server_exceptions=False)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    attachment_bytes = _make_pdf_bytes("丢失缩略图重建", "重新补齐缓存")
    raw_bytes = _make_message_bytes(
        subject="PDF 缩略图重建",
        sender_name="Attach Sender",
        sender_email="attach@example.com",
        to_emails=["reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 12, 41, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="先生成，再删掉缩略图文件",
        html_body="<p>先生成，再删掉缩略图文件</p>",
        attachments=[("rebuild.pdf", "application/pdf", attachment_bytes)],
    )
    FakeImapAdapter.seed_message("user@example.com", "INBOX", "122", raw_bytes)

    detail_response = client.get("/api/folders/INBOX/messages/122")
    assert detail_response.status_code == 200
    attachments = _extract_attachment_list(_extract_detail_payload(detail_response.json()))
    attachment_id, first_thumbnail = _preview_attachment_thumbnail(client, "INBOX", "122", attachments[0], 0)
    assert first_thumbnail.status_code == 200

    thumbnail_files = list(cache_dir.rglob("*.png"))
    assert thumbnail_files
    for path in thumbnail_files:
        path.unlink()

    status_response = client.get(
        f"/api/folders/INBOX/messages/122/attachments/{quote(attachment_id, safe='')}/preview/status"
    )
    assert status_response.status_code == 200
    payload = status_response.json()["data"]
    assert payload["status"] == "ready"
    assert payload["thumbnail_ready"] is True

    FakeImapAdapter.mailboxes.pop(("user@example.com", "INBOX", "122"), None)
    second_thumbnail = client.get(
        f"/api/folders/INBOX/messages/122/attachments/{quote(attachment_id, safe='')}/preview-thumbnail"
    )
    assert second_thumbnail.status_code == 200
    assert second_thumbnail.headers["content-type"].startswith("image/png")


def test_attachment_preview_status_endpoint_returns_ready_or_pending(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    main_module = build_app(monkeypatch, attachment_preview_cache_dir=str(tmp_path / "preview-cache"))
    mailbox_module = importlib.import_module("app.mailbox")
    monkeypatch.setattr(mailbox_module, "MAILBOX_BACKGROUND_EXECUTOR", InlineExecutor())
    client = TestClient(main_module.app, raise_server_exceptions=False)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    attachment_bytes = _make_docx_bytes("状态查询预览", "第二段")
    raw_bytes = _make_message_bytes(
        subject="状态查询",
        sender_name="Attach Sender",
        sender_email="attach@example.com",
        to_emails=["reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 12, 45, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="状态查询正文",
        html_body="<p>状态查询正文</p>",
        attachments=[
            (
                "status.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                attachment_bytes,
            ),
        ],
    )
    FakeImapAdapter.seed_message("user@example.com", "INBOX", "121", raw_bytes)

    detail_response = client.get("/api/folders/INBOX/messages/121")
    assert detail_response.status_code == 200
    attachments = _extract_attachment_list(_extract_detail_payload(detail_response.json()))
    attachment_id = _attachment_candidates(0, attachments[0])[0]

    status_response = client.get(f"/api/folders/INBOX/messages/121/attachments/{quote(attachment_id, safe='')}/preview/status")
    assert status_response.status_code == 200
    payload = status_response.json()["data"]
    assert payload["preview_kind"] == "text"
    assert payload["status"] == "ready"


def test_message_list_prewarms_cached_attachment_preview_before_open(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    main_module = build_app(monkeypatch, attachment_preview_cache_dir=str(tmp_path / "preview-cache"))
    mailbox_module = importlib.import_module("app.mailbox")
    monkeypatch.setattr(mailbox_module, "MAILBOX_BACKGROUND_EXECUTOR", InlineExecutor())
    client = TestClient(main_module.app, raise_server_exceptions=False)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    attachment_bytes = _make_docx_bytes("预热缩略图", "首开前应已生成")
    raw_bytes = _make_message_bytes(
        subject="列表预热附件",
        sender_name="Attach Sender",
        sender_email="attach@example.com",
        to_emails=["reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 12, 46, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="列表预热正文",
        html_body="<p>列表预热正文</p>",
        attachments=[
            (
                "prewarm.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                attachment_bytes,
            ),
        ],
    )
    FakeImapAdapter.seed_message("user@example.com", "INBOX", "131", raw_bytes)

    detail_response = client.get("/api/folders/INBOX/messages/131")
    assert detail_response.status_code == 200

    list_response = client.get("/api/folders/INBOX/messages?page=1&page_size=10&refresh=true")
    assert list_response.status_code == 200

    attachments = _extract_attachment_list(_extract_detail_payload(detail_response.json()))
    attachment_id = _attachment_candidates(0, attachments[0])[0]
    status_response = client.get(f"/api/folders/INBOX/messages/131/attachments/{quote(attachment_id, safe='')}/preview/status")
    assert status_response.status_code == 200
    payload = status_response.json()["data"]
    assert payload["status"] == "ready"
    assert payload["thumbnail_ready"] is True


def test_attachment_preview_cache_cleanup_keeps_directory_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    cache_dir = tmp_path / "bounded-preview-cache"
    main_module = build_app(
        monkeypatch,
        attachment_preview_cache_dir=str(cache_dir),
        attachment_preview_cache_max_mb=1,
    )
    client = TestClient(main_module.app, raise_server_exceptions=False)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    for uid, title in (("130", "预览一"), ("131", "预览二")):
        attachment_bytes = _make_docx_bytes(title, "正文")
        raw_bytes = _make_message_bytes(
            subject=title,
            sender_name="Attach Sender",
            sender_email="attach@example.com",
            to_emails=["reader@example.com"],
            cc_emails=[],
            date_value=datetime(2026, 5, 7, 12, 50, tzinfo=ZoneInfo("Asia/Shanghai")),
            text_body=title,
            html_body=f"<p>{title}</p>",
            attachments=[
                (
                    f"{uid}.docx",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    attachment_bytes,
                ),
            ],
        )
        FakeImapAdapter.seed_message("user@example.com", "INBOX", uid, raw_bytes)
        detail_response = client.get(f"/api/folders/INBOX/messages/{uid}")
        assert detail_response.status_code == 200
        attachments = _extract_attachment_list(_extract_detail_payload(detail_response.json()))
        preview_response = client.get(
            f"/api/folders/INBOX/messages/{uid}/attachments/{quote(_attachment_candidates(0, attachments[0])[0], safe='')}/preview"
        )
        assert preview_response.status_code == 200

    files = [path for path in cache_dir.rglob("*") if path.is_file()]
    total_bytes = sum(path.stat().st_size for path in files)
    assert total_bytes <= 1024 * 1024


def test_attachment_preview_files_are_removed_when_message_deleted(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    cache_dir = tmp_path / "preview-cache"
    main_module = build_app(monkeypatch, attachment_preview_cache_dir=str(cache_dir))
    mailbox_module = importlib.import_module("app.mailbox")
    monkeypatch.setattr(mailbox_module, "MAILBOX_BACKGROUND_EXECUTOR", InlineExecutor())
    client = TestClient(main_module.app, raise_server_exceptions=False)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    attachment_bytes = _make_docx_bytes("删除即清理")
    raw_bytes = _make_message_bytes(
        subject="删除测试",
        sender_name="Attach Sender",
        sender_email="attach@example.com",
        to_emails=["reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="删除正文",
        html_body="<p>删除正文</p>",
        attachments=[
            (
                "cleanup.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                attachment_bytes,
            ),
        ],
    )
    FakeImapAdapter.seed_message("user@example.com", "INBOX", "140", raw_bytes)

    detail_response = client.get("/api/folders/INBOX/messages/140")
    assert detail_response.status_code == 200
    attachments = _extract_attachment_list(_extract_detail_payload(detail_response.json()))
    _preview_attachment(client, "INBOX", "140", attachments[0], 0)
    assert any(path.is_file() for path in cache_dir.rglob("*"))

    delete_response = client.post(
        "/api/messages/delete",
        json={"folder": "INBOX", "uids": ["140"]},
        headers={"X-CSRF-Token": str(client.cookies.get("webmail_csrf") or "")},
    )
    assert delete_response.status_code == 200
    assert not any(path.is_file() for path in cache_dir.rglob("*"))


def test_pdf_thumbnail_files_are_removed_when_message_deleted(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    cache_dir = tmp_path / "preview-cache"
    main_module = build_app(monkeypatch, attachment_preview_cache_dir=str(cache_dir))
    mailbox_module = importlib.import_module("app.mailbox")
    monkeypatch.setattr(mailbox_module, "MAILBOX_BACKGROUND_EXECUTOR", InlineExecutor())
    client = TestClient(main_module.app, raise_server_exceptions=False)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    attachment_bytes = _make_pdf_bytes("删除时清理 PDF 缩略图")
    raw_bytes = _make_message_bytes(
        subject="PDF 删除清理",
        sender_name="Attach Sender",
        sender_email="attach@example.com",
        to_emails=["reader@example.com"],
        cc_emails=[],
        date_value=datetime(2026, 5, 7, 13, 5, tzinfo=ZoneInfo("Asia/Shanghai")),
        text_body="删除后不应残留任何缩略图",
        html_body="<p>删除后不应残留任何缩略图</p>",
        attachments=[("cleanup.pdf", "application/pdf", attachment_bytes)],
    )
    FakeImapAdapter.seed_message("user@example.com", "INBOX", "141", raw_bytes)

    detail_response = client.get("/api/folders/INBOX/messages/141")
    assert detail_response.status_code == 200
    attachments = _extract_attachment_list(_extract_detail_payload(detail_response.json()))
    _preview_attachment_thumbnail(client, "INBOX", "141", attachments[0], 0)
    assert list(cache_dir.rglob("*.png"))

    delete_response = client.post(
        "/api/messages/delete",
        json={"folder": "INBOX", "uids": ["141"]},
        headers={"X-CSRF-Token": str(client.cookies.get("webmail_csrf") or "")},
    )
    assert delete_response.status_code == 200
    assert not any(path.is_file() for path in cache_dir.rglob("*"))


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
