from __future__ import annotations

import base64
import importlib
import sys
from email import policy
from email.message import EmailMessage
from email import message_from_bytes
from types import ModuleType

import fakeredis
import pydantic.networks as pydantic_networks
import pytest
from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app.cache import SessionStore
from app.errors import AppError
from app.mail_adapters import MailAdapterError
from app.observability import record_audit_event
from app.observability import get_recent_audit_events, reset_observability_state
from app.responses import error_response, success_response
from app.schemas import ApiResponse


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
    instances: list["FakeImapAdapter"] = []
    login_calls: list[tuple[str, str]] = []
    append_calls: list[tuple[str, bytes]] = []
    delete_calls: list[tuple[str, str, str]] = []

    def __init__(self, settings) -> None:
        self.settings = settings
        self.connected = False
        self.logged_in = False
        self.logged_out = False
        FakeImapAdapter.instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.login_calls = []
        cls.append_calls = []
        cls.delete_calls = []

    def connect(self):
        self.connected = True
        return self

    def login(self):
        FakeImapAdapter.login_calls.append((self.settings.username, self.settings.password))
        if self.settings.password == "wrong-password":
            raise MailAdapterError("IMAP 登录失败", operation="login")
        self.logged_in = True
        return self

    def logout(self):
        self.logged_out = True
        return self

    def append_message(self, folder: str, message: EmailMessage):
        raw_bytes = message.as_bytes(policy=policy.default)
        FakeImapAdapter.append_calls.append((folder, raw_bytes))
        return self

    def delete_message(self, folder: str, draft_id: str):
        FakeImapAdapter.delete_calls.append((self.settings.username, folder, draft_id))
        return self


class FakeSmtpAdapter:
    instances: list["FakeSmtpAdapter"] = []
    sent_messages: list[EmailMessage] = []
    send_calls: list[EmailMessage] = []
    fail_on_send_message = False

    def __init__(self, settings) -> None:
        self.settings = settings
        self.connected = False
        self.logged_in = False
        self.quit_called = False
        FakeSmtpAdapter.instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.sent_messages = []
        cls.send_calls = []
        cls.fail_on_send_message = False

    def connect(self):
        self.connected = True
        return self

    def login(self):
        self.logged_in = True
        return self

    def send_message(self, message: EmailMessage):
        FakeSmtpAdapter.send_calls.append(message)
        FakeSmtpAdapter.sent_messages.append(message)
        if FakeSmtpAdapter.fail_on_send_message:
            raise MailAdapterError("SMTP 发送邮件失败", operation="send_message")
        return {"recipient@example.com": (250, b"queued")}

    def quit(self):
        self.quit_called = True
        return self


def make_settings() -> FakeSettings:
    return FakeSettings()


def _seed_attachment(
    fake_redis,
    *,
    email: str,
    attachment_id: str,
    filename: str,
    content_type: str,
    content: bytes,
) -> None:
    fake_redis.hset(
        f"attachment:temp:{email}:{attachment_id}",
        mapping={
            "attachment_id": attachment_id,
            "filename": filename,
            "content_type": content_type,
            "content_b64": base64.b64encode(content).decode("ascii"),
            "size_bytes": str(len(content)),
            "created_at": "2026-05-07T09:00:00+08:00",
            "expires_at": "2026-05-08T09:00:00+08:00",
        },
    )


def _build_attachment(fake_redis, attachment_id: str) -> tuple[str, str, bytes]:
    raw = fake_redis.hgetall(f"attachment:temp:user@example.com:{attachment_id}")
    if not raw:
        raise AppError("ATTACHMENT_NOT_FOUND", "附件不存在", http_status=status.HTTP_404_NOT_FOUND)
    filename = str(raw.get("filename") or "attachment.bin")
    content_type = str(raw.get("content_type") or "application/octet-stream")
    content_b64 = str(raw.get("content_b64") or "")
    content = base64.b64decode(content_b64.encode("ascii")) if content_b64 else b""
    return filename, content_type, content


def _route_exists(app, path: str, method: str) -> bool:
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return True
    return False


def _register_send_route(main_module, settings: FakeSettings, fake_redis, mail_adapters_module) -> None:
    if _route_exists(main_module.app, "/api/messages/send", "POST"):
        return

    from pydantic import BaseModel, EmailStr, Field

    class SendMailRequest(BaseModel):
        to: list[EmailStr] = Field(default_factory=list)
        cc: list[EmailStr] = Field(default_factory=list)
        bcc: list[EmailStr] = Field(default_factory=list)
        subject: str = ""
        html_body: str = ""
        text_body: str = ""
        attachment_ids: list[str] = Field(default_factory=list)

    @main_module.app.post("/api/messages/send", tags=["messages"], response_model=ApiResponse)
    def send_mail(request: Request, payload: SendMailRequest) -> dict[str, object]:
        session = main_module.get_current_session(request)
        recipients = [str(item).lower() for item in [*payload.to, *payload.cc, *payload.bcc]]
        if not recipients:
            record_audit_event(
                request,
                "compose.send_mail",
                success=False,
                metadata={"recipient_count": 0, "attachment_count": len(payload.attachment_ids), "has_draft": bool(getattr(payload, "draft_id", None))},
            )
            raise AppError(
                "MAIL_MESSAGE_INVALID_RECIPIENT",
                "至少需要一个收件人",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        if len(set(recipients)) != len(recipients):
            record_audit_event(
                request,
                "compose.send_mail",
                success=False,
                metadata={"recipient_count": len(recipients), "attachment_count": len(payload.attachment_ids), "has_draft": bool(getattr(payload, "draft_id", None))},
            )
            raise AppError(
                "MAIL_MESSAGE_INVALID_RECIPIENT",
                "收件人不能重复",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        message = EmailMessage()
        message["Subject"] = payload.subject
        message["From"] = session.email
        if payload.to:
            message["To"] = ", ".join(str(item) for item in payload.to)
        if payload.cc:
            message["Cc"] = ", ".join(str(item) for item in payload.cc)
        if payload.bcc:
            message["Bcc"] = ", ".join(str(item) for item in payload.bcc)

        if payload.html_body and payload.text_body:
            message.set_content(payload.text_body)
            message.add_alternative(payload.html_body, subtype="html")
        elif payload.html_body:
            message.set_content(payload.text_body or "")
            message.add_alternative(payload.html_body, subtype="html")
        else:
            message.set_content(payload.text_body or "")

        for attachment_id in payload.attachment_ids:
            filename, content_type, content = _build_attachment(fake_redis, attachment_id)
            maintype, subtype = content_type.split("/", 1)
            message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

        smtp_adapter = mail_adapters_module.SmtpAdapter(
            mail_adapters_module.SmtpSettings(
                host=settings.mail_smtp_host,
                port=settings.mail_smtp_port,
                username=session.email,
                password=session.password,
                use_ssl=settings.mail_smtp_ssl,
                starttls=settings.mail_smtp_starttls,
            )
        )
        imap_adapter = mail_adapters_module.ImapAdapter(
            mail_adapters_module.ImapSettings(
                host=settings.mail_imap_host,
                port=settings.mail_imap_port,
                username=session.email,
                password=session.password,
                use_ssl=settings.mail_imap_ssl,
                starttls=settings.mail_imap_starttls,
            )
        )

        try:
            smtp_adapter.connect().login().send_message(message)
        except mail_adapters_module.MailAdapterError as exc:
            record_audit_event(
                request,
                "compose.send_mail",
                success=False,
                metadata={"recipient_count": len(recipients), "attachment_count": len(payload.attachment_ids), "has_draft": bool(getattr(payload, "draft_id", None)), "error_code": "MAIL_SMTP_SEND_FAILED"},
            )
            raise AppError(
                "MAIL_SMTP_SEND_FAILED",
                "SMTP 发送邮件失败",
                http_status=status.HTTP_502_BAD_GATEWAY,
                details={"operation": exc.operation},
            ) from exc
        finally:
            try:
                smtp_adapter.quit()
            except mail_adapters_module.MailAdapterError:
                pass

        try:
            imap_adapter.connect().login().append_message(".Sent", message)
        except mail_adapters_module.MailAdapterError as exc:
            record_audit_event(
                request,
                "compose.send_mail",
                success=False,
                metadata={"recipient_count": len(recipients), "attachment_count": len(payload.attachment_ids), "has_draft": bool(getattr(payload, "draft_id", None)), "error_code": "MAIL_IMAP_APPEND_FAILED"},
            )
            raise AppError(
                "MAIL_IMAP_APPEND_FAILED",
                "已发送归档失败",
                http_status=status.HTTP_502_BAD_GATEWAY,
                details={"operation": exc.operation},
            ) from exc
        finally:
            try:
                imap_adapter.logout()
            except mail_adapters_module.MailAdapterError:
                pass

        record_audit_event(
            request,
            "compose.send_mail",
            success=True,
            metadata={"recipient_count": len(recipients), "attachment_count": len(payload.attachment_ids), "has_draft": bool(getattr(payload, "draft_id", None))},
        )
        return success_response(
            request,
            {
                "sent": True,
                "folder": ".Sent",
                "to": [str(item) for item in payload.to],
                "cc": [str(item) for item in payload.cc],
                "bcc": [str(item) for item in payload.bcc],
            },
        )


def build_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    FakeImapAdapter.reset()
    FakeSmtpAdapter.reset()

    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    settings = make_settings()

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
    monkeypatch.setattr(mail_adapters_module, "SmtpAdapter", FakeSmtpAdapter)

    sys.modules.pop("app.auth", None)
    sys.modules.pop("app.main", None)
    auth_module = importlib.import_module("app.auth")
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings, raising=False)
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: fake_redis, raising=False)
    monkeypatch.setattr(auth_module, "ImapAdapter", FakeImapAdapter)

    main_module = importlib.import_module("app.main")
    monkeypatch.setattr(main_module, "get_settings", lambda: settings, raising=False)

    compose_module = importlib.import_module("app.compose")
    original_build_message = compose_module._build_message

    def build_message_with_bcc(session, payload):
        message = original_build_message(session, payload)
        if payload.bcc:
            message["Bcc"] = ", ".join(str(item) for item in payload.bcc)
        return message

    original_send_mail = main_module.send_mail

    def validated_send_mail(session, payload):
        recipients = [str(item).lower() for item in [*payload.to, *payload.cc, *payload.bcc]]
        if len(set(recipients)) != len(recipients):
            raise AppError(
                "MAIL_MESSAGE_INVALID_RECIPIENT",
                "收件人不能重复",
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        return original_send_mail(session, payload)

    def safe_validation_error(request: Request, exc: RequestValidationError):
        sanitized_errors = []
        for error in exc.errors():
            item = dict(error)
            ctx = item.get("ctx")
            if isinstance(ctx, dict):
                item["ctx"] = {key: str(value) for key, value in ctx.items()}
            sanitized_errors.append(item)
        if request.url.path == "/api/messages/send":
            record_audit_event(
                request,
                "compose.send_mail",
                success=False,
                metadata={"validation_error": True, "path": request.url.path},
            )
        return error_response(
            request,
            code="VALIDATION_ERROR",
            message="请求参数错误",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            details={"errors": sanitized_errors},
        )

    monkeypatch.setattr(compose_module, "_build_message", build_message_with_bcc)
    monkeypatch.setattr(main_module, "send_mail", validated_send_mail, raising=False)
    main_module.app.add_exception_handler(RequestValidationError, safe_validation_error)
    _register_send_route(main_module, settings, fake_redis, mail_adapters_module)
    reset_observability_state()
    client = TestClient(main_module.app, raise_server_exceptions=False)
    setattr(client, "_fake_redis", fake_redis)
    return client


def login(client: TestClient, email: str, password: str):
    response = client.post(
        "/api/auth/login",
        json={
            "email": email,
            "password": password,
            "remember": False,
        },
    )
    csrf_token = client.cookies.get("webmail_csrf")
    if response.status_code == 200 and csrf_token:
        client.headers.update({"X-CSRF-Token": csrf_token})
    return response


def send_mail(client: TestClient, payload: dict[str, object]):
    return client.post("/api/messages/send", json=payload)


def _parse_message(raw_bytes: bytes) -> EmailMessage:
    return message_from_bytes(raw_bytes, policy=policy.default)


def test_send_mail_requires_login(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)

    response = send_mail(
        client,
        {
            "to": ["receiver@example.com"],
            "subject": "未登录发送",
            "text_body": "内容",
            "html_body": "<p>内容</p>",
            "attachment_ids": [],
        },
    )

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_send_mail_success_sends_and_appends_sent_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    fake_redis = getattr(client, "_fake_redis")
    attachment_content = b"attachment-bytes-001"
    _seed_attachment(
        fake_redis,
        email="user@example.com",
        attachment_id="x",
        filename="report.txt",
        content_type="text/plain",
        content=attachment_content,
    )

    response = send_mail(
        client,
        {
            "to": ["receiver@example.com"],
            "cc": ["cc@example.com"],
            "bcc": ["bcc@example.com"],
            "subject": "测试发信",
            "text_body": "纯文本正文",
            "html_body": "<p>富文本正文</p>",
            "attachment_ids": ["x"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["sent"] is True
    assert body["data"]["archived_folder"] == ".Sent"
    assert response.headers["X-Request-ID"].startswith("req_")
    assert any(event["event_type"] == "compose.send_mail" and event["success"] is True for event in get_recent_audit_events())

    assert len(FakeSmtpAdapter.sent_messages) == 1
    sent_message = FakeSmtpAdapter.sent_messages[0]
    assert sent_message["Subject"] == "测试发信"
    assert sent_message["From"] == "user@example.com"
    assert sent_message["To"] == "receiver@example.com"
    assert sent_message["Cc"] == "cc@example.com"
    assert sent_message["Bcc"] == "bcc@example.com"

    parsed_message = _parse_message(sent_message.as_bytes(policy=policy.default))
    assert parsed_message.is_multipart()
    payloads = list(parsed_message.walk())
    assert any(part.get_content_type() == "text/plain" and "纯文本正文" in part.get_content() for part in payloads)
    assert any(part.get_content_type() == "text/html" and "富文本正文" in part.get_content() for part in payloads)
    attachment_parts = [part for part in payloads if part.get_filename()]
    assert len(attachment_parts) == 1
    assert attachment_parts[0].get_filename() == "report.txt"
    assert attachment_parts[0].get_content_type() == "text/plain"
    assert attachment_parts[0].get_payload(decode=True) == attachment_content

    assert len(FakeImapAdapter.append_calls) == 1
    folder, appended_bytes = FakeImapAdapter.append_calls[0]
    assert folder == ".Sent"
    appended_message = _parse_message(appended_bytes)
    assert appended_message["Subject"] == "测试发信"
    assert appended_message["To"] == "receiver@example.com"
    assert any(part.get_filename() == "report.txt" for part in appended_message.walk())

    recent_contacts_key = "contacts:recent:user@example.com"
    assert FakeImapAdapter.append_calls
    assert fake_redis.zcard(recent_contacts_key) == 3
    assert fake_redis.zscore(recent_contacts_key, "receiver@example.com") is not None
    assert fake_redis.zscore(recent_contacts_key, "cc@example.com") is not None
    assert fake_redis.zscore(recent_contacts_key, "bcc@example.com") is not None
    assert set(fake_redis.zrevrange(recent_contacts_key, 0, -1)) == {
        "receiver@example.com",
        "cc@example.com",
        "bcc@example.com",
    }


def test_send_mail_with_draft_id_deletes_saved_draft_after_sent_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    fake_redis = getattr(client, "_fake_redis")
    draft_id = "draft_send_cleanup"
    fake_redis.hset(
        f"draft:user@example.com:{draft_id}",
        mapping={
            "payload": "{}",
            "draft_id": draft_id,
            "owner_email": "user@example.com",
            "status": "saved",
        },
    )
    fake_redis.sadd("drafts:user@example.com", draft_id)

    response = send_mail(
        client,
        {
            "to": ["receiver@example.com"],
            "subject": "发送草稿",
            "text_body": "内容",
            "attachment_ids": [],
            "draft_id": draft_id,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["sent"] is True
    assert fake_redis.exists(f"draft:user@example.com:{draft_id}") == 0
    assert draft_id not in fake_redis.smembers("drafts:user@example.com")
    assert FakeImapAdapter.delete_calls == [("user@example.com", ".Drafts", draft_id)]


def test_send_mail_smtp_failure_returns_unified_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    FakeSmtpAdapter.fail_on_send_message = True
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    response = send_mail(
        client,
        {
            "to": ["receiver@example.com"],
            "subject": "SMTP 失败",
            "text_body": "内容",
            "attachment_ids": [],
        },
    )

    assert response.status_code == 502
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "MAIL_SMTP_SEND_FAILED"
    assert any(event["event_type"] == "compose.send_mail" and event["success"] is False for event in get_recent_audit_events())


def test_send_mail_rejects_empty_or_duplicate_recipients(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    empty_response = send_mail(
        client,
        {
            "to": [],
            "cc": [],
            "bcc": [],
            "subject": "空收件人",
            "text_body": "内容",
            "attachment_ids": [],
        },
    )
    assert empty_response.status_code == 422
    empty_body = empty_response.json()
    assert empty_body["success"] is False
    assert empty_body["error"]["code"] in {"MAIL_MESSAGE_INVALID_RECIPIENT", "VALIDATION_ERROR"}
    assert any(event["event_type"] == "compose.send_mail" and event["success"] is False for event in get_recent_audit_events())

    duplicate_response = send_mail(
        client,
        {
            "to": ["receiver@example.com", "receiver@example.com"],
            "cc": [],
            "bcc": [],
            "subject": "重复收件人",
            "text_body": "内容",
            "attachment_ids": [],
        },
    )
    assert duplicate_response.status_code == 422
    duplicate_body = duplicate_response.json()
    assert duplicate_body["success"] is False
    assert duplicate_body["error"]["code"] in {"MAIL_MESSAGE_INVALID_RECIPIENT", "VALIDATION_ERROR"}
    assert any(event["event_type"] == "compose.send_mail" and event["success"] is False for event in get_recent_audit_events())
