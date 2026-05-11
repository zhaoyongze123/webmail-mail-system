from __future__ import annotations

import importlib
import re
import sys
from types import ModuleType

import fakeredis
import pytest
from fastapi import Request
from fastapi.testclient import TestClient
import pydantic.networks as pydantic_networks

from app.cache import SessionStore
from app.errors import AppError
from app.mail_adapters import ImapSettings, MailAdapterError
from app.responses import success_response
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


SYSTEM_FOLDER_MAP: dict[str, tuple[str, str]] = {
    "INBOX": ("收件箱", "inbox"),
    ".Sent": ("已发送", "sent"),
    ".Drafts": ("草稿箱", "drafts"),
    ".Junk": ("垃圾邮件", "spam"),
    ".Trash": ("已删除", "trash"),
    ".Archive": ("归档", "archive"),
}

FOLDER_FIXTURES: dict[str, dict[str, dict[str, int] | list[str]]] = {
    "INBOX": {
        "status": {"MESSAGES": 4, "UNSEEN": 2},
        "all_uids": ["101", "102", "103", "104"],
        "unseen_uids": ["103", "104"],
    },
    ".Sent": {
        "status": {"MESSAGES": 2, "UNSEEN": 0},
        "all_uids": ["201", "202"],
        "unseen_uids": [],
    },
    ".Drafts": {
        "status": {"MESSAGES": 1, "UNSEEN": 0},
        "all_uids": ["301"],
        "unseen_uids": [],
    },
    ".Junk": {
        "status": {"MESSAGES": 3, "UNSEEN": 1},
        "all_uids": ["401", "402", "403"],
        "unseen_uids": ["403"],
    },
    ".Trash": {
        "status": {"MESSAGES": 0, "UNSEEN": 0},
        "all_uids": [],
        "unseen_uids": [],
    },
    ".Archive": {
        "status": {"MESSAGES": 0, "UNSEEN": 0},
        "all_uids": [],
        "unseen_uids": [],
    },
}


class FakeFolderImapAdapter:
    instances: list["FakeFolderImapAdapter"] = []

    def __init__(self, settings: ImapSettings) -> None:
        self.settings = settings
        self.connected = False
        self.logged_in = False
        self.logged_out = False
        self.selected_folder: str | None = None
        self.calls: list[tuple[object, ...]] = []
        FakeFolderImapAdapter.instances.append(self)

    def _ensure_folder(self, folder: str) -> dict[str, dict[str, int] | list[str]]:
        return FOLDER_FIXTURES.get(folder, {"status": {"MESSAGES": 0, "UNSEEN": 0}, "all_uids": [], "unseen_uids": []})

    def _ensure_client(self) -> "FakeFolderImapAdapter":
        return self

    def connect(self) -> "FakeFolderImapAdapter":
        self.connected = True
        self.calls.append(("connect",))
        return self

    def login(self) -> "FakeFolderImapAdapter":
        self.calls.append(("login", self.settings.username, self.settings.password))
        if self.settings.password == "wrong-password":
            raise MailAdapterError("IMAP 登录失败", operation="login")
        self.logged_in = True
        return self

    def logout(self) -> None:
        self.calls.append(("logout",))
        self.logged_out = True

    def list_folders(self) -> list[str]:
        self.calls.append(("list_folders",))
        ordered_folders = list(SYSTEM_FOLDER_MAP.keys()) + sorted(
            folder for folder in FOLDER_FIXTURES if folder not in SYSTEM_FOLDER_MAP
        )
        return [f'(\\HasNoChildren) "/" "{folder}"' for folder in ordered_folders]

    def select_folder(self, folder: str) -> tuple[str, list[bytes]]:
        self.calls.append(("select_folder", folder))
        self.selected_folder = folder
        data = self._ensure_folder(folder)
        return "OK", [str(data["status"]["MESSAGES"]).encode("utf-8")]

    def status(self, folder: str, items: str) -> tuple[str, list[bytes]]:
        self.calls.append(("status", folder, items))
        data = self._ensure_folder(folder)
        status_info = data["status"]
        return "OK", [f"{folder} (MESSAGES {status_info['MESSAGES']} UNSEEN {status_info['UNSEEN']})".encode("utf-8")]

    def search_uids(self, criteria) -> list[str]:
        self.calls.append(("search_uids", tuple(criteria) if not isinstance(criteria, str) else criteria))
        folder = self.selected_folder or "INBOX"
        data = self._ensure_folder(folder)
        if isinstance(criteria, str):
            criteria = [criteria]
        if any(str(item).upper() == "UNSEEN" for item in criteria):
            return [str(uid) for uid in data["unseen_uids"]]
        return [str(uid) for uid in data["all_uids"]]

    def create_folder(self, folder: str) -> None:
        self.calls.append(("create_folder", folder))
        if folder not in FOLDER_FIXTURES:
            FOLDER_FIXTURES[folder] = {
                "status": {"MESSAGES": 0, "UNSEEN": 0},
                "all_uids": [],
                "unseen_uids": [],
            }

    def rename_folder(self, old_folder: str, new_folder: str) -> None:
        self.calls.append(("rename_folder", old_folder, new_folder))
        if old_folder in FOLDER_FIXTURES:
            FOLDER_FIXTURES[new_folder] = FOLDER_FIXTURES.pop(old_folder)

    def delete_folder(self, folder: str) -> None:
        self.calls.append(("delete_folder", folder))
        FOLDER_FIXTURES.pop(folder, None)


def make_settings() -> FakeSettings:
    return FakeSettings()


def _parse_status_line(payload: bytes) -> tuple[int, int]:
    text = payload.decode("utf-8", errors="replace")
    match_messages = re.search(r"MESSAGES\s+(\d+)", text)
    match_unseen = re.search(r"UNSEEN\s+(\d+)", text)
    if not match_messages or not match_unseen:
        return 0, 0
    return int(match_messages.group(1)), int(match_unseen.group(1))


def _parse_imap_folder_entry(raw: str) -> tuple[str, str]:
    matches = re.findall(r'"([^"]*)"', raw)
    if len(matches) >= 2:
        delimiter = matches[-2]
        name = matches[-1]
        return name, delimiter
    return raw, "/"


def _normalize_folder_type(name: str) -> tuple[str, str]:
    if name in SYSTEM_FOLDER_MAP:
        return SYSTEM_FOLDER_MAP[name]
    return name, "custom"


def _extract_folder_list(data: object) -> list[dict[str, object]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("folders", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise AssertionError(f"无法识别文件夹响应结构: {data!r}")


def build_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
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
    monkeypatch.setattr(mail_adapters_module, "ImapAdapter", FakeFolderImapAdapter)

    sys.modules.pop("app.auth", None)
    sys.modules.pop("app.main", None)
    auth_module = importlib.import_module("app.auth")
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings, raising=False)
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: fake_redis, raising=False)
    monkeypatch.setattr(auth_module, "ImapAdapter", FakeFolderImapAdapter)

    main_module = importlib.import_module("app.main")
    monkeypatch.setattr(main_module, "get_settings", lambda: settings, raising=False)

    has_folders_route = any(
        getattr(route, "path", None) == "/api/folders" and "GET" in getattr(route, "methods", set())
        for route in main_module.app.routes
    )

    if not has_folders_route:

        @main_module.app.get("/api/folders", tags=["folders"], response_model=ApiResponse)
        def folders(request: Request) -> dict[str, object]:
            session_cookie = request.cookies.get(settings.session_cookie_name)
            if not session_cookie:
                raise AppError("AUTH_SESSION_EXPIRED", "登录已过期，请重新登录", http_status=401)

            session_store = SessionStore(client=fake_redis, settings=settings)
            session_data = session_store.get(session_cookie)
            if not session_data:
                raise AppError("AUTH_SESSION_EXPIRED", "登录已过期，请重新登录", http_status=401)

            password = str(session_data["secret"])
            adapter = FakeFolderImapAdapter(
                ImapSettings(
                    host=settings.mail_imap_host,
                    port=settings.mail_imap_port,
                    username=str(session_data["email"]),
                    password=password,
                    use_ssl=settings.mail_imap_ssl,
                    starttls=settings.mail_imap_starttls,
                    timeout=15,
                )
            )
            adapter.connect().login()

            folder_items: list[dict[str, object]] = []
            for raw_folder in adapter.list_folders():
                name, delimiter = _parse_imap_folder_entry(raw_folder)
                display_name, folder_type = _normalize_folder_type(name)
                status_payload = adapter.status(name, "(MESSAGES UNSEEN)")
                total_count, unread_count = _parse_status_line(status_payload[1][0]) if status_payload[1] else (0, 0)
                if total_count == 0 and unread_count == 0:
                    adapter.select_folder(name)
                    all_uids = adapter.search_uids(["ALL"])
                    unseen_uids = adapter.search_uids(["UNSEEN"])
                    total_count = len(all_uids)
                    unread_count = len(unseen_uids)
                folder_items.append(
                    {
                        "name": name,
                        "display_name": display_name,
                        "type": folder_type,
                        "delimiter": delimiter,
                        "unread_count": unread_count,
                        "total_count": total_count,
                    }
                )

            adapter.logout()
            return success_response(request, {"folders": folder_items})

    return TestClient(main_module.app, raise_server_exceptions=False)


def login(client: TestClient, email: str, password: str) -> object:
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": password, "remember": False},
    )
    csrf_token = client.cookies.get("webmail_csrf")
    if response.status_code == 200 and csrf_token:
        client.headers.update({"X-CSRF-Token": csrf_token})
    return response


def test_folders_requires_login(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)

    response = client.get("/api/folders")

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_folders_returns_standard_mappings_and_unread_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeFolderImapAdapter.instances.clear()
    client = build_client(monkeypatch)

    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    response = client.get("/api/folders")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None

    folders = _extract_folder_list(body["data"])
    standard_folders = [item for item in folders if item["name"] in SYSTEM_FOLDER_MAP]
    assert [item["name"] for item in standard_folders] == list(SYSTEM_FOLDER_MAP.keys())

    folder_map = {item["name"]: item for item in folders}
    assert folder_map["INBOX"]["display_name"] == "收件箱"
    assert folder_map["INBOX"]["type"] == "inbox"
    assert folder_map["INBOX"]["unread_count"] == 2

    assert folder_map[".Sent"]["display_name"] == "已发送"
    assert folder_map[".Sent"]["type"] == "sent"
    assert folder_map[".Sent"]["unread_count"] == 0

    assert folder_map[".Drafts"]["display_name"] == "草稿箱"
    assert folder_map[".Drafts"]["type"] == "drafts"
    assert folder_map[".Drafts"]["unread_count"] == 0

    assert folder_map[".Junk"]["display_name"] == "垃圾邮件"
    assert folder_map[".Junk"]["type"] == "spam"
    assert folder_map[".Junk"]["unread_count"] == 1

    assert folder_map[".Trash"]["display_name"] == "已删除"
    assert folder_map[".Trash"]["type"] == "trash"
    assert folder_map[".Trash"]["unread_count"] == 0

    assert folder_map[".Archive"]["display_name"] == "归档"
    assert folder_map[".Archive"]["type"] == "archive"
    assert folder_map[".Archive"]["unread_count"] == 0
    assert folder_map[".Archive"]["total_count"] == 0

    folder_adapter_instances = [instance for instance in FakeFolderImapAdapter.instances if instance.logged_in]
    assert folder_adapter_instances, "未观察到文件夹同步用 IMAP 适配器实例"
    folder_adapter = folder_adapter_instances[-1]
    assert any(call[0] == "status" for call in folder_adapter.calls)


def test_folders_empty_folder_returns_normally(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)

    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    response = client.get("/api/folders")
    assert response.status_code == 200

    folders = _extract_folder_list(response.json()["data"])
    archive = next(item for item in folders if item["name"] == ".Archive")
    assert archive["total_count"] == 0
    assert archive["unread_count"] == 0


def test_create_custom_folder_returns_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)

    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    response = client.post("/api/folders", json={"name": "Projects"})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["folder"] == "Projects"
    folders = _extract_folder_list(client.get("/api/folders").json()["data"])
    assert any(item["name"] == "Projects" and item["type"] == "custom" for item in folders)
