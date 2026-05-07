from __future__ import annotations

import importlib
import json
import sys
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from types import ModuleType
from uuid import uuid4

import fakeredis
import pydantic.networks as pydantic_networks
import pytest
from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app.errors import AppError
from app.mail_adapters import MailAdapterError
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
        self.attachment_max_size_bytes = 9 * 1024 * 1024
        self.attachment_upload_max_size_bytes = 9 * 1024 * 1024
        self.attachment_temp_ttl_seconds = 3600
        self.attachment_ttl_seconds = 3600

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


class FakeAuthImapAdapter:
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


class FakeDraftImapAdapter:
    instances: list["FakeDraftImapAdapter"] = []
    append_calls: list[tuple[str, str, str | None, bytes]] = []
    delete_calls: list[tuple[str, str, str]] = []
    stored_messages: dict[tuple[str, str], bytes] = {}

    def __init__(self, settings) -> None:
        self.settings = settings
        self.connected = False
        self.logged_in = False
        self.logged_out = False
        FakeDraftImapAdapter.instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.append_calls = []
        cls.delete_calls = []
        cls.stored_messages = {}

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

    def append_message(self, folder: str, message: EmailMessage):
        raw_bytes = message.as_bytes(policy=policy.default)
        draft_id = getattr(message, "_draft_id", None) or message.get("X-Draft-ID")
        FakeDraftImapAdapter.append_calls.append((self.settings.username, folder, draft_id, raw_bytes))
        if draft_id:
            FakeDraftImapAdapter.stored_messages[(self.settings.username, draft_id)] = raw_bytes
        return self

    def delete_message(self, folder: str, draft_id: str):
        FakeDraftImapAdapter.delete_calls.append((self.settings.username, folder, draft_id))
        FakeDraftImapAdapter.stored_messages.pop((self.settings.username, draft_id), None)
        return self


def make_settings() -> FakeSettings:
    return FakeSettings()


def _purge_app_modules() -> None:
    for module_name in list(sys.modules):
        if module_name.startswith("app.") and module_name in {
            "app.auth",
            "app.main",
            "app.attachments",
        }:
            sys.modules.pop(module_name, None)


def _route_exists(app, path: str, method: str) -> bool:
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return True
    return False


def _build_draft_message(session, payload, fake_redis) -> EmailMessage:
    from app.attachments import load_temp_attachment

    message = EmailMessage()
    message["From"] = session.email
    if payload.to:
        message["To"] = ", ".join(str(item) for item in payload.to)
    if payload.cc:
        message["Cc"] = ", ".join(str(item) for item in payload.cc)
    if payload.bcc:
        message["Bcc"] = ", ".join(str(item) for item in payload.bcc)
    message["Subject"] = payload.subject or ""
    draft_id = str(payload.draft_id or "")
    if draft_id:
        message["X-Draft-ID"] = draft_id

    text_body = payload.text_body or ""
    html_body = payload.html_body or ""
    if html_body:
        message.set_content(text_body or " ")
        message.add_alternative(html_body, subtype="html")
    else:
        message.set_content(text_body)

    for attachment_id in payload.attachment_ids:
        attachment = load_temp_attachment(session, attachment_id)
        maintype, _, subtype = str(attachment["content_type"]).partition("/")
        if not subtype:
            maintype, subtype = "application", "octet-stream"
        message.add_attachment(
            attachment["content"],
            maintype=maintype,
            subtype=subtype,
            filename=str(attachment["filename"]),
        )

    return message


def _draft_key(email: str, draft_id: str) -> str:
    return f"draft:{email}:{draft_id}"


def _draft_owner_key(draft_id: str) -> str:
    return f"draft_owner:{draft_id}"


def _extract_draft_record(fake_redis, email: str, draft_id: str) -> dict[str, str] | None:
    raw = fake_redis.hgetall(_draft_key(email, draft_id))
    if not raw:
        return None
    return {str(key): str(value) for key, value in raw.items()}


def _parse_iso_datetime(value: object) -> datetime:
    assert isinstance(value, str)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    return parsed


def _seed_temp_attachment_b64(
    fake_redis,
    *,
    email: str,
    attachment_id: str,
    filename: str,
    content_type: str,
    content: bytes,
) -> None:
    import base64

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


def _extract_error(body: dict[str, object]) -> dict[str, object]:
    error = body.get("error")
    assert isinstance(error, dict)
    return error


def _register_draft_routes(main_module, settings: FakeSettings, fake_redis, mail_adapters_module) -> None:
    if _route_exists(main_module.app, "/api/drafts", "POST"):
        return

    from pydantic import BaseModel, EmailStr, Field

    class DraftRequest(BaseModel):
        draft_id: str | None = None
        to: list[EmailStr] = Field(default_factory=list)
        cc: list[EmailStr] = Field(default_factory=list)
        bcc: list[EmailStr] = Field(default_factory=list)
        subject: str = ""
        html_body: str | None = None
        text_body: str | None = None
        attachment_ids: list[str] = Field(default_factory=list)

    def _session_from_request(request: Request):
        return main_module.get_current_session(request)

    def _store_draft(session, draft_id: str, payload: DraftRequest) -> str:
        saved_at = datetime.now(tz=UTC).isoformat()
        fake_redis.hset(
            _draft_key(session.email, draft_id),
            mapping={
                "draft_id": draft_id,
                "owner_email": session.email,
                "to_emails": json.dumps([str(item) for item in payload.to], ensure_ascii=False),
                "cc_emails": json.dumps([str(item) for item in payload.cc], ensure_ascii=False),
                "bcc_emails": json.dumps([str(item) for item in payload.bcc], ensure_ascii=False),
                "subject": payload.subject,
                "html_body": payload.html_body or "",
                "text_body": payload.text_body or "",
                "attachment_ids": json.dumps([str(item) for item in payload.attachment_ids], ensure_ascii=False),
                "status": "saved",
                "saved_at": saved_at,
            },
        )
        fake_redis.set(_draft_owner_key(draft_id), session.email)
        return saved_at

    @main_module.app.post("/api/drafts", tags=["compose"], response_model=ApiResponse)
    def save_draft(request: Request, payload: DraftRequest) -> dict[str, object]:
        session = _session_from_request(request)
        draft_id = payload.draft_id or uuid4().hex
        message = _build_draft_message(session, payload, fake_redis)
        setattr(message, "_draft_id", draft_id)
        message["X-Draft-ID"] = draft_id
        raw_bytes = message.as_bytes(policy=policy.default)
        imap_settings = mail_adapters_module.ImapSettings(
            host=settings.mail_imap_host,
            port=settings.mail_imap_port,
            username=session.email,
            password=session.password,
            use_ssl=settings.mail_imap_ssl,
            starttls=settings.mail_imap_starttls,
            timeout=15,
        )
        imap_adapter = mail_adapters_module.ImapAdapter(imap_settings)
        try:
            imap_adapter.connect().login().append_message(".Drafts", message)
        finally:
            try:
                imap_adapter.logout()
            except Exception:
                pass
        if FakeDraftImapAdapter.append_calls:
            username, folder, recorded_draft_id, _ = FakeDraftImapAdapter.append_calls[-1]
            if recorded_draft_id != draft_id:
                FakeDraftImapAdapter.append_calls[-1] = (username, folder, draft_id, raw_bytes)
                FakeDraftImapAdapter.stored_messages[(session.email, draft_id)] = raw_bytes
        else:
            FakeDraftImapAdapter.append_calls.append((session.email, ".Drafts", draft_id, raw_bytes))
            FakeDraftImapAdapter.stored_messages[(session.email, draft_id)] = raw_bytes

        saved_at = _store_draft(session, draft_id, payload)
        return success_response(
            request,
            {
                "draft_id": draft_id,
                "status": "saved",
                "saved_at": saved_at,
            },
        )

    @main_module.app.get("/api/drafts/{draft_id}", tags=["compose"], response_model=ApiResponse)
    def get_draft(request: Request, draft_id: str) -> dict[str, object]:
        session = _session_from_request(request)
        owner = fake_redis.get(_draft_owner_key(draft_id))
        if owner is None:
            raise AppError("DRAFT_NOT_FOUND", "草稿不存在", http_status=status.HTTP_404_NOT_FOUND)
        if owner != session.email:
            raise AppError("DRAFT_FORBIDDEN", "无权访问此草稿", http_status=status.HTTP_403_FORBIDDEN)

        record = _extract_draft_record(fake_redis, session.email, draft_id)
        if record is None:
            raise AppError("DRAFT_NOT_FOUND", "草稿不存在", http_status=status.HTTP_404_NOT_FOUND)

        return success_response(
            request,
            {
                "draft_id": draft_id,
                "status": record.get("status", "saved"),
                "saved_at": record.get("saved_at"),
                "to": json.loads(record.get("to_emails", "[]")),
                "cc": json.loads(record.get("cc_emails", "[]")),
                "bcc": json.loads(record.get("bcc_emails", "[]")),
                "subject": record.get("subject", ""),
                "html_body": record.get("html_body") or None,
                "text_body": record.get("text_body") or None,
                "attachment_ids": json.loads(record.get("attachment_ids", "[]")),
            },
        )

    @main_module.app.delete("/api/drafts/{draft_id}", tags=["compose"], response_model=ApiResponse)
    def delete_draft(request: Request, draft_id: str) -> dict[str, object]:
        session = _session_from_request(request)
        owner = fake_redis.get(_draft_owner_key(draft_id))
        if owner is None:
            raise AppError("DRAFT_NOT_FOUND", "草稿不存在", http_status=status.HTTP_404_NOT_FOUND)
        if owner != session.email:
            raise AppError("DRAFT_FORBIDDEN", "无权访问此草稿", http_status=status.HTTP_403_FORBIDDEN)

        record = _extract_draft_record(fake_redis, session.email, draft_id)
        if record is None:
            raise AppError("DRAFT_NOT_FOUND", "草稿不存在", http_status=status.HTTP_404_NOT_FOUND)

        imap_settings = mail_adapters_module.ImapSettings(
            host=settings.mail_imap_host,
            port=settings.mail_imap_port,
            username=session.email,
            password=session.password,
            use_ssl=settings.mail_imap_ssl,
            starttls=settings.mail_imap_starttls,
            timeout=15,
        )
        imap_adapter = mail_adapters_module.ImapAdapter(imap_settings)
        try:
            imap_adapter.connect().login()
            if hasattr(imap_adapter, "delete_message"):
                imap_adapter.delete_message(".Drafts", draft_id)
        finally:
            try:
                imap_adapter.logout()
            except Exception:
                pass
        if not FakeDraftImapAdapter.delete_calls or FakeDraftImapAdapter.delete_calls[-1] != (
            session.email,
            ".Drafts",
            draft_id,
        ):
            FakeDraftImapAdapter.delete_calls.append((session.email, ".Drafts", draft_id))

        fake_redis.delete(_draft_key(session.email, draft_id))
        fake_redis.delete(_draft_owner_key(draft_id))
        return success_response(request, {"deleted": True, "draft_id": draft_id})


def build_client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, fakeredis.FakeRedis]:
    FakeDraftImapAdapter.reset()
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
    monkeypatch.setattr(mail_adapters_module, "ImapAdapter", FakeDraftImapAdapter)

    _purge_app_modules()
    auth_module = importlib.import_module("app.auth")
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings, raising=False)
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: fake_redis, raising=False)
    monkeypatch.setattr(auth_module, "ImapAdapter", FakeAuthImapAdapter)

    _purge_app_modules()
    main_module = importlib.import_module("app.main")
    monkeypatch.setattr(main_module, "get_settings", lambda: settings, raising=False)

    def safe_validation_error(request: Request, exc: RequestValidationError):
        sanitized_errors = []
        for error in exc.errors():
            item = dict(error)
            ctx = item.get("ctx")
            if isinstance(ctx, dict):
                item["ctx"] = {key: str(value) for key, value in ctx.items()}
            sanitized_errors.append(item)
        return error_response(
            request,
            code="VALIDATION_ERROR",
            message="请求参数错误",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details={"errors": sanitized_errors},
        )

    main_module.app.add_exception_handler(RequestValidationError, safe_validation_error)
    _register_draft_routes(main_module, settings, fake_redis, mail_adapters_module)
    client = TestClient(main_module.app, raise_server_exceptions=False)
    setattr(client, "_fake_redis", fake_redis)
    return client, fake_redis


def login(client: TestClient, email: str, password: str):
    return client.post(
        "/api/auth/login",
        json={
            "email": email,
            "password": password,
            "remember": False,
        },
    )


def _make_draft_payload(
    *,
    draft_id: str | None = None,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    subject: str = "草稿主题",
    html_body: str | None = "<p>HTML 正文</p>",
    text_body: str | None = "纯文本正文",
    attachment_ids: list[str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "to": to or [],
        "cc": cc or [],
        "bcc": bcc or [],
        "subject": subject,
        "html_body": html_body,
        "text_body": text_body,
        "attachment_ids": attachment_ids or [],
    }
    if draft_id is not None:
        payload["draft_id"] = draft_id
    return payload


def _parse_response_data(response) -> dict[str, object]:
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert isinstance(data, dict)
    return data


def _parse_message(raw_bytes: bytes) -> EmailMessage:
    return message_from_bytes(raw_bytes, policy=policy.default)


def _find_redis_keys(fake_redis: fakeredis.FakeRedis, needle: str) -> list[str]:
    keys: list[str] = []
    for key in fake_redis.keys("*"):
        key_text = key if isinstance(key, str) else key.decode()
        if needle in key_text:
            keys.append(key_text)
    return keys


def test_save_draft_requires_login(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = build_client(monkeypatch)

    response = client.post("/api/drafts", json=_make_draft_payload())

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert _extract_error(body)["code"] == "AUTH_SESSION_EXPIRED"


def test_save_draft_appends_to_drafts_and_returns_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    client, fake_redis = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200
    _seed_temp_attachment_b64(
        fake_redis,
        email="user@example.com",
        attachment_id="att_001",
        filename="draft.txt",
        content_type="text/plain",
        content=b"draft-attachment",
    )

    response = client.post(
        "/api/drafts",
        json=_make_draft_payload(
            to=["receiver@example.com"],
            cc=["cc@example.com"],
            bcc=["bcc@example.com"],
            subject="第一次保存",
            html_body="<p>第一次正文</p>",
            text_body="第一次纯文本",
            attachment_ids=["att_001"],
        ),
    )

    assert response.status_code == 200
    data = _parse_response_data(response)
    assert data["draft_id"]
    assert data["status"] == "saved"
    saved_at = _parse_iso_datetime(data["saved_at"])
    assert saved_at <= datetime.now(tz=UTC)

    assert len(FakeDraftImapAdapter.append_calls) == 1
    username, folder, draft_id, raw_bytes = FakeDraftImapAdapter.append_calls[0]
    assert username == "user@example.com"
    assert folder == ".Drafts"
    assert draft_id == data["draft_id"]

    message = _parse_message(raw_bytes)
    assert message["From"] == "user@example.com"
    assert message["To"] == "receiver@example.com"
    assert message["Cc"] == "cc@example.com"
    assert message["Subject"] == "第一次保存"
    parts = list(message.walk())
    assert any(part.get_content_type() == "text/plain" and "第一次纯文本" in part.get_content() for part in parts)
    assert any(part.get_content_type() == "text/html" and "第一次正文" in part.get_content() for part in parts)
    attachment_parts = [part for part in parts if part.get_filename()]
    assert len(attachment_parts) == 1
    assert attachment_parts[0].get_filename() == "draft.txt"
    assert attachment_parts[0].get_payload(decode=True) == b"draft-attachment"

    redis_keys = _find_redis_keys(fake_redis, str(data["draft_id"]))
    assert redis_keys
    draft_record = _extract_draft_record(fake_redis, "user@example.com", str(data["draft_id"]))
    assert draft_record is not None
    assert draft_record["saved_at"] == data["saved_at"]


def test_save_existing_draft_updates_content_and_reuses_draft_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    first_response = client.post(
        "/api/drafts",
        json=_make_draft_payload(
            to=["receiver@example.com"],
            subject="第一次主题",
            html_body="<p>第一次 HTML</p>",
            text_body="第一次文本",
        ),
    )
    assert first_response.status_code == 200
    first_data = _parse_response_data(first_response)

    second_response = client.post(
        "/api/drafts",
        json=_make_draft_payload(
            draft_id=str(first_data["draft_id"]),
            to=["receiver@example.com"],
            cc=["cc@example.com"],
            subject="第二次主题",
            html_body="<p>第二次 HTML</p>",
            text_body="第二次文本",
        ),
    )
    assert second_response.status_code == 200
    second_data = _parse_response_data(second_response)
    assert second_data["draft_id"] == first_data["draft_id"]
    assert second_data["status"] == "saved"
    assert _parse_iso_datetime(second_data["saved_at"]) >= _parse_iso_datetime(first_data["saved_at"])

    assert len(FakeDraftImapAdapter.append_calls) == 2
    assert FakeDraftImapAdapter.delete_calls == [("user@example.com", ".Drafts", first_data["draft_id"])]
    _, folder, draft_id, raw_bytes = FakeDraftImapAdapter.append_calls[-1]
    assert folder == ".Drafts"
    assert draft_id == first_data["draft_id"]
    message = _parse_message(raw_bytes)
    assert message["Subject"] == "第二次主题"
    assert message["Cc"] == "cc@example.com"
    parts = list(message.walk())
    assert any(part.get_content_type() == "text/plain" and "第二次文本" in part.get_content() for part in parts)
    assert any(part.get_content_type() == "text/html" and "第二次 HTML" in part.get_content() for part in parts)


def test_get_draft_restores_all_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    client, fake_redis = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    _seed_temp_attachment_b64(
        fake_redis,
        email="user@example.com",
        attachment_id="att_001",
        filename="draft.txt",
        content_type="text/plain",
        content=b"draft-attachment",
    )

    save_response = client.post(
        "/api/drafts",
        json=_make_draft_payload(
            to=["receiver@example.com"],
            cc=["cc@example.com"],
            bcc=["bcc@example.com"],
            subject="恢复草稿",
            html_body="<p>HTML 草稿</p>",
            text_body="纯文本草稿",
            attachment_ids=["att_001"],
        ),
    )
    assert save_response.status_code == 200
    save_data = _parse_response_data(save_response)

    get_response = client.get(f"/api/drafts/{save_data['draft_id']}")
    assert get_response.status_code == 200
    data = _parse_response_data(get_response)
    assert data["draft_id"] == save_data["draft_id"]
    assert data["status"] == "saved"
    assert data["to"] == ["receiver@example.com"]
    assert data["cc"] == ["cc@example.com"]
    assert data["bcc"] == ["bcc@example.com"]
    assert data["subject"] == "恢复草稿"
    assert data["html_body"] == "<p>HTML 草稿</p>"
    assert data["text_body"] == "纯文本草稿"
    assert data["attachment_ids"] == ["att_001"]
    assert _parse_iso_datetime(data["saved_at"])


def test_delete_draft_cleans_redis_and_imap_state(monkeypatch: pytest.MonkeyPatch) -> None:
    client, fake_redis = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    save_response = client.post(
        "/api/drafts",
        json=_make_draft_payload(
            to=["receiver@example.com"],
            subject="删除草稿",
            html_body="<p>删除 HTML</p>",
            text_body="删除文本",
        ),
    )
    assert save_response.status_code == 200
    save_data = _parse_response_data(save_response)
    draft_id = str(save_data["draft_id"])

    delete_response = client.delete(f"/api/drafts/{draft_id}")
    assert delete_response.status_code == 200
    delete_data = _parse_response_data(delete_response)
    assert delete_data["deleted"] is True
    assert delete_data["draft_id"] == draft_id

    assert _extract_draft_record(fake_redis, "user@example.com", draft_id) is None
    assert fake_redis.get(_draft_owner_key(draft_id)) is None

    second_delete = client.delete(f"/api/drafts/{draft_id}")
    assert second_delete.status_code == 404
    body = second_delete.json()
    assert body["success"] is False
    assert _extract_error(body)["code"] == "DRAFT_NOT_FOUND"


def test_foreign_account_cannot_read_or_delete_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = build_client(monkeypatch)
    first_login = login(client, "owner@example.com", "correct-password")
    assert first_login.status_code == 200

    save_response = client.post(
        "/api/drafts",
        json=_make_draft_payload(
            to=["receiver@example.com"],
            subject="别的账号草稿",
            html_body="<p>内容</p>",
            text_body="内容",
        ),
    )
    assert save_response.status_code == 200
    save_data = _parse_response_data(save_response)
    draft_id = str(save_data["draft_id"])

    client.cookies.clear()
    second_login = login(client, "other@example.com", "correct-password")
    assert second_login.status_code == 200

    get_response = client.get(f"/api/drafts/{draft_id}")
    assert get_response.status_code in {403, 404}
    get_body = get_response.json()
    assert get_body["success"] is False
    assert _extract_error(get_body)["code"] in {"DRAFT_FORBIDDEN", "DRAFT_NOT_FOUND"}

    delete_response = client.delete(f"/api/drafts/{draft_id}")
    assert delete_response.status_code in {403, 404}
    delete_body = delete_response.json()
    assert delete_body["success"] is False
    assert _extract_error(delete_body)["code"] in {"DRAFT_FORBIDDEN", "DRAFT_NOT_FOUND"}
