from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import default

import pytest

from app.auth import AuthSession
from app.mail_adapters import MailAdapterError


@dataclass
class FakeCompatMessage:
    uid: str
    raw_bytes: bytes
    flags: set[str]


class FakeCompatAdapter:
    def __init__(self) -> None:
        self.selected_folder: str | None = None
        self.messages = {
            "INBOX": {
                "1": FakeCompatMessage("1", _make_message("周报", "这里是 release note", "alice@example.com"), set()),
                "2": FakeCompatMessage("2", _make_message("通知", "普通内容", "bob@example.com"), set()),
            },
            "Trash": {},
            "Archive": {},
            "Work": {},
        }
        self.copy_calls: list[tuple[str, str]] = []
        self.store_calls: list[tuple[str, str, str]] = []
        self.expunge_calls = 0
        self.search_calls: list[str] = []
        self.create_calls: list[str] = []
        self.rename_calls: list[tuple[str, str]] = []
        self.delete_calls: list[str] = []

    def list_folders(self) -> list[str]:
        return [
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "Trash"',
            '(\\HasNoChildren) "/" "Archive"',
            '(\\HasNoChildren) "/" "Work"',
        ]

    def status(self, folder: str, items: str):
        if folder not in self.messages:
            self.messages[folder] = {}
        return "OK", [f"{folder} (MESSAGES 0 UNSEEN 0 UIDVALIDITY 1)".encode("utf-8")]

    def select_folder(self, folder: str):
        self.selected_folder = folder
        return "OK", [b"1"]

    def uid_search(self, criteria: str):
        self.search_calls.append(criteria)
        if criteria != "ALL":
            raise MailAdapterError("真实服务器不接受自由文本 UID SEARCH", operation="uid_search")
        return list(self.messages.get(self.selected_folder or "", {}).keys())

    def uid_fetch_message_bytes(self, uid: str):
        return self.messages[self.selected_folder or ""][uid].raw_bytes

    def copy_message(self, uid: str, target_folder: str) -> None:
        self.copy_calls.append((uid, target_folder))
        source = self.messages[self.selected_folder or ""][uid]
        self.messages[target_folder][uid] = FakeCompatMessage(uid, source.raw_bytes, set(source.flags))

    def store_flags(self, uid: str, command: str, flags: str) -> None:
        self.store_calls.append((uid, command, flags))
        if command == "+FLAGS":
            self.messages[self.selected_folder or ""][uid].flags.add(flags.strip("()"))

    def expunge(self) -> None:
        self.expunge_calls += 1
        folder_messages = self.messages[self.selected_folder or ""]
        for uid in list(folder_messages.keys()):
            if "\\Deleted" in folder_messages[uid].flags:
                del folder_messages[uid]

    def logout(self) -> None:
        return None

    def create_folder(self, folder: str) -> None:
        self.create_calls.append(folder)
        self.messages.setdefault(folder, {})

    def rename_folder(self, old_folder: str, new_folder: str) -> None:
        self.rename_calls.append((old_folder, new_folder))
        self.messages[new_folder] = self.messages.pop(old_folder, {})

    def delete_folder(self, folder: str) -> None:
        self.delete_calls.append(folder)
        self.messages.pop(folder, None)


def _make_message(subject: str, body: str, sender_email: str) -> bytes:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"Sender <{sender_email}>"
    message["To"] = "team@example.com"
    message.set_content(body)
    return message.as_bytes(policy=default)


def _session() -> AuthSession:
    return AuthSession(
        session_id="test-session",
        email="test@mdaemon.cc",
        password="123456",
        imap={},
        smtp={},
        preferences={},
    )


def test_search_messages_falls_back_to_local_filter_for_real_imap(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = FakeCompatAdapter()
    monkeypatch.setattr("app.mailbox._connect_imap", lambda session: adapter)
    from app.mailbox import search_messages

    page = search_messages(_session(), "INBOX", "release", refresh=True)

    assert adapter.search_calls == ["ALL"]
    assert page.total == 1
    assert [message["uid"] for message in page.messages] == ["1"]


def test_operate_messages_delete_uses_resolved_trash_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = FakeCompatAdapter()
    monkeypatch.setattr("app.mailbox._connect_imap", lambda session: adapter)
    monkeypatch.setattr("app.mailbox._remove_message_records", lambda email, folder, uids: None)
    from app.mailbox import MessageOperationRequest, operate_messages

    payload = MessageOperationRequest(action="delete", uids=["1"])
    result = operate_messages(_session(), "INBOX", payload)

    assert adapter.copy_calls == [("1", "Trash")]
    assert adapter.expunge_calls == 1
    assert "1" not in adapter.messages["INBOX"]
    assert "1" in adapter.messages["Trash"]
    assert result["target_folder"] is None


def test_operate_messages_move_resolves_canonical_target_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = FakeCompatAdapter()
    monkeypatch.setattr("app.mailbox._connect_imap", lambda session: adapter)
    monkeypatch.setattr("app.mailbox._remove_message_records", lambda email, folder, uids: None)
    from app.mailbox import MessageOperationRequest, operate_messages

    payload = MessageOperationRequest(action="move", uids=["2"], target_folder=".Archive")
    result = operate_messages(_session(), "INBOX", payload)

    assert adapter.copy_calls == [("2", "Archive")]
    assert adapter.expunge_calls == 1
    assert "2" not in adapter.messages["INBOX"]
    assert "2" in adapter.messages["Archive"]
    assert result["target_folder"] == "Archive"


def test_list_messages_refresh_moves_blacklisted_sender_to_trash(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = FakeCompatAdapter()
    monkeypatch.setattr("app.mailbox._connect_imap", lambda session: adapter)
    monkeypatch.setattr("app.mailbox.list_blacklisted_contacts", lambda session: ["alice@example.com"])
    monkeypatch.setattr("app.mailbox.list_whitelisted_contacts", lambda session: [])
    monkeypatch.setattr("app.mailbox._remove_message_records", lambda email, folder, uids: None)

    from app.mailbox import list_messages

    page = list_messages(_session(), "INBOX", page=1, page_size=10, refresh=True)

    assert adapter.copy_calls == [("1", "Trash")]
    assert adapter.expunge_calls == 1
    assert "1" not in adapter.messages["INBOX"]
    assert "1" in adapter.messages["Trash"]
    assert [message["uid"] for message in page.messages] == ["2"]


def test_list_messages_refresh_whitelist_overrides_blacklist(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = FakeCompatAdapter()
    monkeypatch.setattr("app.mailbox._connect_imap", lambda session: adapter)
    monkeypatch.setattr("app.mailbox.list_blacklisted_contacts", lambda session: ["alice@example.com"])
    monkeypatch.setattr("app.mailbox.list_whitelisted_contacts", lambda session: ["alice@example.com"])
    monkeypatch.setattr("app.mailbox._remove_message_records", lambda email, folder, uids: None)

    from app.mailbox import list_messages

    page = list_messages(_session(), "INBOX", page=1, page_size=10, refresh=True)

    assert adapter.copy_calls == []
    assert adapter.expunge_calls == 0
    assert [message["uid"] for message in page.messages] == ["2", "1"]


def test_folder_create_rename_delete_use_imap_folder_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = FakeCompatAdapter()
    monkeypatch.setattr("app.mailbox._connect_imap", lambda session: adapter)
    from app.mailbox import create_folder, delete_folder, rename_folder

    create_folder(_session(), "Drafts 2026")
    adapter.messages["Drafts 2026"] = {}
    rename_folder(_session(), "Drafts 2026", "Drafts 2027")
    delete_folder(_session(), "Drafts 2027")

    assert adapter.create_calls == ["Drafts 2026"]
    assert adapter.rename_calls == [("Drafts 2026", "Drafts 2027")]
    assert adapter.delete_calls == ["Drafts 2027"]
