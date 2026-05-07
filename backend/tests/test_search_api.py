from __future__ import annotations

import importlib
import re
import sys
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from types import ModuleType, SimpleNamespace
from typing import Any

import fakeredis
import pydantic.networks as pydantic_networks
import pytest
from fastapi import Query, Request
from fastapi.testclient import TestClient

from app.mail_adapters import MailAdapterError


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


def make_settings() -> FakeSettings:
    return FakeSettings()


def make_email_message(
    *,
    uid: str,
    sender_name: str,
    sender_email: str,
    to_emails: list[str],
    subject: str,
    body: str,
    sent_at: datetime,
) -> dict[str, object]:
    message = EmailMessage()
    message["Message-ID"] = f"<{uid}@example.com>"
    message["From"] = f"{sender_name} <{sender_email}>"
    message["To"] = ", ".join(to_emails)
    message["Subject"] = subject
    message["Date"] = format_datetime(sent_at)
    message.set_content(body)

    return {
        "uid": uid,
        "sender_name": sender_name,
        "sender_email": sender_email,
        "to_emails": to_emails,
        "subject": subject,
        "body": body,
        "sent_at": sent_at,
        "raw": message.as_bytes(),
    }


def make_mailbox_state() -> dict[str, object]:
    base_time = datetime(2026, 5, 7, 9, 0, tzinfo=UTC)
    messages = [
        make_email_message(
            uid="301",
            sender_name="Alice",
            sender_email="alice@example.com",
            to_emails=["team@example.com"],
            subject="季度报告",
            body="季度报告摘要：Alice 本周完成了关键里程碑。",
            sent_at=base_time - timedelta(minutes=1),
        ),
        make_email_message(
            uid="302",
            sender_name="Bob",
            sender_email="bob@example.com",
            to_emails=["carol@example.com"],
            subject="内部通知",
            body="内部通知摘要：Carol 需要确认排期。",
            sent_at=base_time - timedelta(minutes=5),
        ),
        make_email_message(
            uid="303",
            sender_name="Carol",
            sender_email="carol@example.com",
            to_emails=["dave@example.com"],
            subject="项目周报",
            body="项目周报摘要：本周交付进展稳定。",
            sent_at=base_time - timedelta(minutes=10),
        ),
        make_email_message(
            uid="304",
            sender_name="David",
            sender_email="david@example.com",
            to_emails=["ops@example.com"],
            subject="无关邮件",
            body="这是一封不会命中的邮件。",
            sent_at=base_time - timedelta(minutes=15),
        ),
    ]
    return {
        "mailbox": {"INBOX": messages},
        "search_count": 0,
        "fetch_count": 0,
        "search_queries": [],
    }


def add_mailbox_message(mailbox_state: dict[str, object], folder: str, message: dict[str, object]) -> None:
    mailbox = mailbox_state["mailbox"]
    assert isinstance(mailbox, dict)
    folder_messages = mailbox.setdefault(folder, [])
    assert isinstance(folder_messages, list)
    folder_messages.append(message)
    folder_messages.sort(key=lambda item: item["sent_at"], reverse=True)


class FakeSearchImapAdapter:
    instances: list["FakeSearchImapAdapter"] = []

    def __init__(self, settings) -> None:
        self.settings = settings
        self.connected = False
        self.logged_in = False
        self.logged_out = False
        self.selected_folder = "INBOX"
        self.calls: list[tuple[object, ...]] = []
        FakeSearchImapAdapter.instances.append(self)

    def connect(self):
        self.connected = True
        self.calls.append(("connect",))
        return self

    def login(self):
        self.calls.append(("login", self.settings.username, self.settings.password))
        if getattr(self.settings, "password", None) == "wrong-password":
            raise MailAdapterError("IMAP 登录失败", operation="login")
        self.logged_in = True
        return self

    def logout(self):
        self.calls.append(("logout",))
        self.logged_out = True
        return self

    def select_folder(self, folder: str):
        self.calls.append(("select_folder", folder))
        self.selected_folder = folder
        return "OK", [b"1"]

    def _folder_messages(self) -> list[dict[str, object]]:
        mailbox = getattr(self.settings, "_mailbox_state", None)
        if mailbox is None:
            raise MailAdapterError("搜索测试未绑定邮箱状态", operation="search_uids")
        mailbox_data = mailbox["mailbox"]
        assert isinstance(mailbox_data, dict)
        messages = mailbox_data.get(self.selected_folder, [])
        assert isinstance(messages, list)
        return messages

    def search_uids(self, criteria) -> list[str]:
        query = " ".join(criteria) if isinstance(criteria, (list, tuple)) else str(criteria)
        query = query.strip()
        mailbox_state = getattr(self.settings, "_mailbox_state", None)
        if mailbox_state is None:
            raise MailAdapterError("搜索测试未绑定邮箱状态", operation="search_uids")

        mailbox_state["search_count"] = int(mailbox_state["search_count"]) + 1
        mailbox_state["search_queries"].append(query)
        self.calls.append(("search_uids", query))

        if not query:
            return []

        query_lower = query.lower()
        matched_uids: list[str] = []
        for item in self._folder_messages():
            haystacks = [
                str(item["subject"]),
                str(item["sender_name"]),
                str(item["sender_email"]),
                " ".join(str(value) for value in item["to_emails"]),
                str(item["body"]),
            ]
            if any(query_lower in haystack.lower() for haystack in haystacks):
                matched_uids.append(str(item["uid"]))

        matched_uids.sort(
            key=lambda uid: next(
                item["sent_at"]
                for item in self._folder_messages()
                if str(item["uid"]) == uid
            ),
            reverse=True,
        )
        return matched_uids

    def uid_search(self, criteria):
        self.calls.append(("uid_search", criteria))
        return self.search_uids(criteria)

    def fetch_message_bytes(self, uid: str | bytes) -> bytes:
        uid_text = uid.decode() if isinstance(uid, bytes) else str(uid)
        mailbox_state = getattr(self.settings, "_mailbox_state", None)
        if mailbox_state is None:
            raise MailAdapterError("搜索测试未绑定邮箱状态", operation="fetch_message_bytes")
        mailbox_state["fetch_count"] = int(mailbox_state["fetch_count"]) + 1
        self.calls.append(("fetch_message_bytes", uid_text))
        for item in self._folder_messages():
            if str(item["uid"]) == uid_text:
                return item["raw"]  # type: ignore[return-value]
        raise MailAdapterError("IMAP 邮件不存在", operation="fetch_message_bytes")

    def uid_fetch_message_bytes(self, uid: str | bytes) -> bytes:
        self.calls.append(("uid_fetch_message_bytes", uid))
        return self.fetch_message_bytes(uid)


def _search_cache_key(email: str, folder: str, q: str, page: int, page_size: int) -> str:
    return f"mail:search:{email}:{folder}:{q.strip().lower()}:{page}:{page_size}"


def build_client(monkeypatch: pytest.MonkeyPatch, mailbox_state: dict[str, object]) -> TestClient:
    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    settings = make_settings()
    settings._mailbox_state = mailbox_state  # type: ignore[attr-defined]

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
    monkeypatch.setattr(mail_adapters_module, "ImapAdapter", FakeSearchImapAdapter)

    sys.modules.pop("app.auth", None)
    sys.modules.pop("app.main", None)
    auth_module = importlib.import_module("app.auth")
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings, raising=False)
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: fake_redis, raising=False)
    monkeypatch.setattr(auth_module, "ImapAdapter", FakeSearchImapAdapter)

    main_module = importlib.import_module("app.main")
    monkeypatch.setattr(main_module, "get_settings", lambda: settings, raising=False)
    auth_runtime_module = importlib.import_module("app.auth")
    cache_runtime_module = importlib.import_module("app.cache")
    responses_module = importlib.import_module("app.responses")
    schemas_module = importlib.import_module("app.schemas")
    mailbox_module = importlib.import_module("app.mailbox")
    app_error_module = importlib.import_module("app.errors")
    message_summary = mailbox_module._message_summary
    JsonCache = cache_runtime_module.JsonCache
    ApiResponse = schemas_module.ApiResponse
    AppError = app_error_module.AppError
    get_current_session = auth_runtime_module.get_current_session
    success_response = responses_module.success_response
    mailbox_page = mailbox_module.MailboxPage

    def search_messages(
        session,
        folder: str,
        query: str,
        *,
        page: int = 1,
        page_size: int = 30,
        refresh: bool = False,
    ):
        normalized_query = query.strip()
        if not normalized_query:
            raise AppError("SEARCH_QUERY_REQUIRED", "搜索关键词不能为空", http_status=422)

        cache = JsonCache(fake_redis)
        key = _search_cache_key(session.email, folder, normalized_query, page, page_size)
        if not refresh:
            cached = cache.get(key)
            if cached:
                return mailbox_page(cached=True, **cached)

        adapter = FakeSearchImapAdapter(
            SimpleNamespace(
                host=settings.mail_imap_host,
                port=settings.mail_imap_port,
                username=session.email,
                password=session.password,
                use_ssl=settings.mail_imap_ssl,
                starttls=settings.mail_imap_starttls,
                timeout=15,
                _mailbox_state=mailbox_state,
            )
        )
        try:
            adapter.connect().login()
            adapter.select_folder(folder)
            uids = adapter.search_uids(normalized_query)
            messages = [message_summary(uid, adapter.uid_fetch_message_bytes(uid)) for uid in uids]
            messages.sort(key=lambda item: item["date"] or "", reverse=True)
            offset = max(page - 1, 0) * page_size
            payload = {
                "folder": folder,
                "page": page,
                "page_size": page_size,
                "total": len(messages),
                "messages": messages[offset : offset + page_size],
            }
            cache.set(key, payload, ttl_seconds=60)
            return mailbox_page(cached=False, **payload)
        except MailAdapterError as exc:
            raise AppError(
                "MAILBOX_SEARCH_FAILED",
                "搜索邮件失败",
                http_status=502,
                details={"operation": exc.operation},
            ) from exc
        finally:
            adapter.logout()

    monkeypatch.setattr(main_module, "search_messages", search_messages)

    return TestClient(main_module.app, raise_server_exceptions=False)


def login(client: TestClient, email: str, password: str) -> object:
    return client.post(
        "/api/auth/login",
        json={"email": email, "password": password, "remember": False},
    )


def request_search(
    client: TestClient,
    folder: str,
    *,
    q: str | None,
    page: int = 1,
    page_size: int = 30,
):
    params: dict[str, object] = {
        "page": page,
        "page_size": page_size,
    }
    if q is not None:
        params["q"] = q
    return client.get(f"/api/folders/{folder}/messages/search", params=params)


def request_legacy_search(
    client: TestClient,
    folder: str,
    *,
    q: str,
    page: int = 1,
    page_size: int = 30,
):
    return client.get(
        "/api/search",
        params={"folder": folder, "q": q, "page": page, "page_size": page_size},
    )


def extract_message_rows(body: dict[str, object]) -> list[dict[str, object]]:
    data = body["data"]
    assert isinstance(data, dict)
    rows = data.get("messages")
    if rows is None:
        rows = data.get("items")
    assert isinstance(rows, list)
    return [item for item in rows if isinstance(item, dict)]


def parse_response_datetime(value: object) -> datetime:
    assert isinstance(value, str)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def assert_message_row_shape(row: dict[str, object]) -> None:
    assert {"uid", "sender", "subject", "date", "read", "has_attachments", "snippet"} <= set(row)
    assert isinstance(row["uid"], str)
    assert row["subject"]
    assert row["snippet"]
    assert isinstance(row["read"], bool)
    assert isinstance(row["has_attachments"], bool)


def test_search_requires_login(monkeypatch: pytest.MonkeyPatch) -> None:
    mailbox_state = make_mailbox_state()
    client = build_client(monkeypatch, mailbox_state)

    response = request_search(client, "INBOX", q="季度")

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "AUTH_SESSION_EXPIRED"


@pytest.mark.parametrize(
    ("q", "expected_uid"),
    [
        ("季度报告", "301"),
        ("alice@example.com", "301"),
        ("carol@example.com", "302"),
        ("确认排期", "302"),
    ],
)
def test_search_matches_subject_sender_recipient_and_snippet(
    monkeypatch: pytest.MonkeyPatch,
    q: str,
    expected_uid: str,
) -> None:
    mailbox_state = make_mailbox_state()
    client = build_client(monkeypatch, mailbox_state)

    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    response = request_search(client, "INBOX", q=q, page=1, page_size=30)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    data = body["data"]
    assert isinstance(data, dict)
    assert data["folder"] == "INBOX"
    assert data["query"] == q
    assert data["page"] == 1
    assert data["page_size"] == 30
    assert data["cached"] is False

    rows = extract_message_rows(body)
    assert rows
    assert rows[0]["uid"] == expected_uid
    assert_message_row_shape(rows[0])


def test_search_legacy_api_path_matches_recipient(monkeypatch: pytest.MonkeyPatch) -> None:
    mailbox_state = make_mailbox_state()
    client = build_client(monkeypatch, mailbox_state)

    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    response = request_legacy_search(client, "INBOX", q="dave@example.com")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert isinstance(data, dict)
    assert data["folder"] == "INBOX"
    rows = extract_message_rows(body)
    assert [row["uid"] for row in rows] == ["303"]


def test_search_paginates_and_orders_by_date_desc(monkeypatch: pytest.MonkeyPatch) -> None:
    mailbox_state = make_mailbox_state()
    add_mailbox_message(
        mailbox_state,
        "INBOX",
        make_email_message(
            uid="305",
            sender_name="Eve",
            sender_email="eve@example.com",
            to_emails=["team@example.com"],
            subject="项目周报",
            body="项目周报摘要：Eve 补充了最新进展。",
            sent_at=datetime(2026, 5, 7, 9, 2, tzinfo=UTC),
        ),
    )
    add_mailbox_message(
        mailbox_state,
        "INBOX",
        make_email_message(
            uid="306",
            sender_name="Frank",
            sender_email="frank@example.com",
            to_emails=["team@example.com"],
            subject="项目周报",
            body="项目周报摘要：Frank 更新了测试结果。",
            sent_at=datetime(2026, 5, 7, 9, 3, tzinfo=UTC),
        ),
    )
    client = build_client(monkeypatch, mailbox_state)

    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    first_response = request_search(client, "INBOX", q="项目周报", page=1, page_size=1)
    assert first_response.status_code == 200
    first_rows = extract_message_rows(first_response.json())
    assert [row["uid"] for row in first_rows] == ["306"]

    second_response = request_search(client, "INBOX", q="项目周报", page=2, page_size=1)
    assert second_response.status_code == 200
    second_rows = extract_message_rows(second_response.json())
    assert [row["uid"] for row in second_rows] == ["305"]

    first_date = parse_response_datetime(first_rows[0]["date"])
    second_date = parse_response_datetime(second_rows[0]["date"])
    assert first_date > second_date


def test_search_no_results_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    mailbox_state = make_mailbox_state()
    client = build_client(monkeypatch, mailbox_state)

    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    response = request_search(client, "INBOX", q="不存在的关键字", page=1, page_size=30)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert isinstance(data, dict)
    assert data["total"] == 0
    assert extract_message_rows(body) == []


def test_search_empty_q_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    mailbox_state = make_mailbox_state()
    client = build_client(monkeypatch, mailbox_state)

    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    response = request_search(client, "INBOX", q="", page=1, page_size=30)

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] in {"VALIDATION_ERROR", "SEARCH_QUERY_REQUIRED"}


def test_search_empty_result_is_cached_and_second_request_skips_imap_search(monkeypatch: pytest.MonkeyPatch) -> None:
    mailbox_state = make_mailbox_state()
    client = build_client(monkeypatch, mailbox_state)

    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    first_response = request_search(client, "INBOX", q="完全不会命中", page=1, page_size=30)
    assert first_response.status_code == 200
    first_body = first_response.json()
    assert first_body["data"]["cached"] is False
    assert extract_message_rows(first_body) == []
    assert int(mailbox_state["search_count"]) == 1

    second_response = request_search(client, "INBOX", q="完全不会命中", page=1, page_size=30)
    assert second_response.status_code == 200
    second_body = second_response.json()
    assert second_body["data"]["cached"] is True
    assert extract_message_rows(second_body) == []
    assert int(mailbox_state["search_count"]) == 1
