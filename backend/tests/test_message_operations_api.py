from __future__ import annotations

import importlib
import re
import sys
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from types import ModuleType
from typing import Any

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
    store_calls: list[tuple[str, str, str, str | None]] = []
    copy_calls: list[tuple[str, str, str | None]] = []
    move_calls: list[tuple[str, str, str | None]] = []
    expunge_calls: list[str | None] = []
    generic_calls: list[tuple[str, tuple[Any, ...], dict[str, Any], str | None]] = []
    selected_folders: list[str] = []
    last_instance: "FakeImapAdapter | None" = None

    def __init__(self, settings) -> None:
        self.settings = settings
        self.connected = False
        self.logged_in = False
        self.logged_out = False
        self.selected_folder: str | None = None
        FakeImapAdapter.last_instance = self

    @classmethod
    def reset(cls) -> None:
        cls.mailboxes = {}
        cls.login_calls = []
        cls.store_calls = []
        cls.copy_calls = []
        cls.move_calls = []
        cls.expunge_calls = []
        cls.generic_calls = []
        cls.selected_folders = []
        cls.last_instance = None

    @classmethod
    def seed_message(
        cls,
        folder: str,
        uid: str,
        *,
        flags: set[str] | None = None,
        raw_bytes: bytes | None = None,
    ) -> None:
        cls.mailboxes[(folder, uid)] = FakeMailboxMessage(
            folder=folder,
            uid=uid,
            raw_bytes=raw_bytes or _make_message_bytes(folder=folder, uid=uid),
            flags=set(flags or set()),
        )

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

    def store(self, uid: str | bytes, command: str, flags: str):
        uid_str = uid.decode("utf-8") if isinstance(uid, bytes) else str(uid)
        FakeImapAdapter.store_calls.append((uid_str, command, flags, self.selected_folder))
        message = self.mailboxes.get((self.selected_folder or "", uid_str))
        if message is not None:
            normalized_flag = flags.strip("()")
            if command == "+FLAGS":
                message.flags.add(normalized_flag)
            elif command == "-FLAGS":
                message.flags.discard(normalized_flag)
        return "OK", [b"FLAGS"]

    def copy(self, uid: str | bytes, folder: str):
        uid_str = uid.decode("utf-8") if isinstance(uid, bytes) else str(uid)
        FakeImapAdapter.copy_calls.append((uid_str, folder, self.selected_folder))
        source = self.mailboxes.get((self.selected_folder or "", uid_str))
        if source is not None:
            self.mailboxes[(folder, uid_str)] = FakeMailboxMessage(
                folder=folder,
                uid=uid_str,
                raw_bytes=source.raw_bytes,
                flags=set(source.flags),
            )
        return "OK", [b"OK"]

    def move(self, uid: str | bytes, folder: str):
        uid_str = uid.decode("utf-8") if isinstance(uid, bytes) else str(uid)
        FakeImapAdapter.move_calls.append((uid_str, folder, self.selected_folder))
        source_key = (self.selected_folder or "", uid_str)
        source = self.mailboxes.get(source_key)
        if source is not None:
            self.mailboxes[(folder, uid_str)] = FakeMailboxMessage(
                folder=folder,
                uid=uid_str,
                raw_bytes=source.raw_bytes,
                flags=set(source.flags),
            )
            del self.mailboxes[source_key]
        return "OK", [b"OK"]

    def expunge(self):
        FakeImapAdapter.expunge_calls.append(self.selected_folder)
        if self.selected_folder is not None:
            for key, message in list(self.mailboxes.items()):
                if key[0] == self.selected_folder and "\\Deleted" in message.flags:
                    del self.mailboxes[key]
        return "OK", [b"OK"]

    def uid(self, command: str, uid: str | bytes, *args: Any):
        command_text = str(command).upper()
        if command_text == "STORE":
            return self.store(uid, str(args[0]), str(args[1]) if len(args) > 1 else "")
        if command_text == "COPY":
            return self.copy(uid, str(args[0]))
        if command_text == "MOVE":
            return self.move(uid, str(args[0]))
        if command_text == "EXPUNGE":
            return self.expunge()
        FakeImapAdapter.generic_calls.append((f"uid:{command_text}", (uid, *args), {}, self.selected_folder))
        return "OK", [b"OK"]

    def __getattr__(self, name: str):
        def recorder(*args: Any, **kwargs: Any):
            FakeImapAdapter.generic_calls.append((name, args, kwargs, self.selected_folder))
            upper_name = name.upper()
            if "STORE" in upper_name and len(args) >= 3:
                return self.store(args[0], str(args[1]), str(args[2]))
            if "COPY" in upper_name and len(args) >= 2:
                return self.copy(args[0], str(args[1]))
            if "MOVE" in upper_name and len(args) >= 2:
                return self.move(args[0], str(args[1]))
            if "EXPUNGE" in upper_name:
                return self.expunge()
            return "OK", [b"OK"]

        return recorder


def make_settings(*, login_fail_limit: int = 5) -> FakeSettings:
    return FakeSettings(login_fail_limit=login_fail_limit)


def _make_message_bytes(*, folder: str, uid: str) -> bytes:
    message = EmailMessage()
    message["Subject"] = f"测试邮件 {folder}-{uid}"
    message["From"] = "sender@example.com"
    message["To"] = "recipient@example.com"
    message["Message-ID"] = f"<{folder}-{uid}@example.com>"
    message.set_content(f"这是 {folder} 文件夹中 UID {uid} 的测试内容。")
    return message.as_bytes(policy=policy.default)


def _extract_error(body: dict[str, object]) -> dict[str, object]:
    error = body.get("error")
    assert isinstance(error, dict)
    return error


def _purge_app_modules() -> None:
    for module_name in list(sys.modules):
        if module_name.startswith("app.") and (
            module_name in {"app.main", "app.auth", "app.mailbox", "app.attachments"}
        ):
            sys.modules.pop(module_name, None)


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

    _purge_app_modules()
    auth_module = importlib.import_module("app.auth")
    monkeypatch.setattr(auth_module, "get_settings", lambda: settings, raising=False)
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: fake_redis, raising=False)
    monkeypatch.setattr(auth_module, "ImapAdapter", FakeImapAdapter)

    _purge_app_modules()
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


def _request_operation(
    client: TestClient,
    folder: str,
    *,
    action: str,
    uids: list[str],
    target_folder: str | None = None,
):
    payload: dict[str, object] = {
        "action": action,
        "uids": uids,
        "target_folder": target_folder,
    }
    return client.post(f"/api/folders/{folder}/messages/operations", json=payload)


def _request_legacy_single_operation(client: TestClient, folder: str, uid: str, action: str):
    return client.post(f"/api/messages/{folder}/{uid}/{action}")


def _request_legacy_bulk_operation(
    client: TestClient,
    path: str,
    *,
    folder: str,
    uids: list[str],
    target_folder: str | None = None,
):
    payload: dict[str, object] = {"folder": folder, "uids": uids}
    if target_folder is not None:
        payload["target_folder"] = target_folder
    return client.post(path, json=payload)


def _assert_success(response) -> dict[str, object]:
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert isinstance(body["data"], object)
    return body


def _normalize_flag_value(value: str) -> str:
    return value.strip().strip("()")


@pytest.mark.parametrize(
    "action,expected_command,expected_flag",
    [
        ("mark_read", "+FLAGS", "\\Seen"),
        ("mark_unread", "-FLAGS", "\\Seen"),
    ],
)
def test_message_operations_mark_read_and_mark_unread_apply_to_all_uids(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    expected_command: str,
    expected_flag: str,
) -> None:
    client = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    FakeImapAdapter.seed_message("INBOX", "101")
    FakeImapAdapter.seed_message("INBOX", "102", flags={"\\Seen"})

    response = _request_operation(client, "INBOX", action=action, uids=["101", "102"])
    _assert_success(response)

    assert len(FakeImapAdapter.store_calls) == 2
    assert {call[0] for call in FakeImapAdapter.store_calls} == {"101", "102"}
    assert all(call[1] == expected_command for call in FakeImapAdapter.store_calls)
    assert all(_normalize_flag_value(call[2]) == expected_flag for call in FakeImapAdapter.store_calls)
    if action == "mark_read":
        assert expected_flag in FakeImapAdapter.mailboxes[("INBOX", "101")].flags
        assert expected_flag in FakeImapAdapter.mailboxes[("INBOX", "102")].flags
    else:
        assert expected_flag not in FakeImapAdapter.mailboxes[("INBOX", "101")].flags
        assert expected_flag not in FakeImapAdapter.mailboxes[("INBOX", "102")].flags


@pytest.mark.parametrize("action", ["flag", "star"])
def test_message_operations_flag_and_star_set_flagged_flag(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    client = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    FakeImapAdapter.seed_message("INBOX", "201")
    FakeImapAdapter.seed_message("INBOX", "202")

    response = _request_operation(client, "INBOX", action=action, uids=["201", "202"])
    _assert_success(response)

    assert len(FakeImapAdapter.store_calls) == 2
    assert {call[0] for call in FakeImapAdapter.store_calls} == {"201", "202"}
    assert all(call[1] == "+FLAGS" for call in FakeImapAdapter.store_calls)
    assert all(_normalize_flag_value(call[2]) == "\\Flagged" for call in FakeImapAdapter.store_calls)
    assert "\\Flagged" in FakeImapAdapter.mailboxes[("INBOX", "201")].flags
    assert "\\Flagged" in FakeImapAdapter.mailboxes[("INBOX", "202")].flags


def test_message_operations_move_moves_all_uids_to_target_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    FakeImapAdapter.seed_message("INBOX", "301")
    FakeImapAdapter.seed_message("INBOX", "302")

    response = _request_operation(
        client,
        "INBOX",
        action="move",
        uids=["301", "302"],
        target_folder=".Archive",
    )
    _assert_success(response)

    assert len(FakeImapAdapter.move_calls) + len(FakeImapAdapter.copy_calls) >= 2
    assert all(call[1] == ".Archive" for call in FakeImapAdapter.move_calls + FakeImapAdapter.copy_calls)
    assert ("INBOX", "301") not in FakeImapAdapter.mailboxes
    assert ("INBOX", "302") not in FakeImapAdapter.mailboxes
    assert (".Archive", "301") in FakeImapAdapter.mailboxes
    assert (".Archive", "302") in FakeImapAdapter.mailboxes


def test_message_operations_delete_uses_trash_or_deleted_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    FakeImapAdapter.seed_message("INBOX", "401")
    FakeImapAdapter.seed_message("INBOX", "402")

    response = _request_operation(client, "INBOX", action="delete", uids=["401", "402"])
    _assert_success(response)

    trash_uids = {uid for (folder, uid) in FakeImapAdapter.mailboxes if folder == ".Trash"}
    deleted_flags = [
        message.flags
        for (folder, _uid), message in FakeImapAdapter.mailboxes.items()
        if folder == "INBOX"
    ]
    moved_to_trash = trash_uids >= {"401", "402"} or any(call[1] == ".Trash" for call in FakeImapAdapter.move_calls)
    deleted_marked = all("\\Deleted" in flags for flags in deleted_flags) and bool(FakeImapAdapter.expunge_calls)
    assert moved_to_trash or deleted_marked


def test_message_operations_legacy_read_and_unread_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200
    FakeImapAdapter.seed_message("INBOX", "451")

    read_response = _request_legacy_single_operation(client, "INBOX", "451", "read")
    _assert_success(read_response)
    unread_response = _request_legacy_single_operation(client, "INBOX", "451", "unread")
    _assert_success(unread_response)

    assert FakeImapAdapter.store_calls[0][:3] == ("451", "+FLAGS", "(\\Seen)")
    assert FakeImapAdapter.store_calls[1][:3] == ("451", "-FLAGS", "(\\Seen)")


def test_message_operations_legacy_move_and_delete_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200
    FakeImapAdapter.seed_message("INBOX", "461")
    FakeImapAdapter.seed_message("INBOX", "462")

    move_response = _request_legacy_bulk_operation(
        client,
        "/api/messages/move",
        folder="INBOX",
        uids=["461"],
        target_folder=".Archive",
    )
    _assert_success(move_response)
    delete_response = _request_legacy_bulk_operation(
        client,
        "/api/messages/delete",
        folder="INBOX",
        uids=["462"],
    )
    _assert_success(delete_response)

    assert (".Archive", "461") in FakeImapAdapter.mailboxes
    assert any(call[1] == ".Trash" for call in FakeImapAdapter.copy_calls + FakeImapAdapter.move_calls)


def test_message_operations_requires_login(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_client(monkeypatch)

    response = _request_operation(client, "INBOX", action="mark_read", uids=["501"])

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert _extract_error(body)["code"] == "AUTH_SESSION_EXPIRED"


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "invalid_action", "uids": ["601"], "target_folder": None},
        {"action": "mark_read", "uids": [], "target_folder": None},
    ],
)
def test_message_operations_reject_invalid_action_and_empty_uids(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    client = build_client(monkeypatch)
    login_response = login(client, "user@example.com", "correct-password")
    assert login_response.status_code == 200

    response = client.post("/api/folders/INBOX/messages/operations", json=payload)

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert _extract_error(body)["code"] in {"VALIDATION_ERROR", "BAD_REQUEST", "INVALID_ACTION"}
