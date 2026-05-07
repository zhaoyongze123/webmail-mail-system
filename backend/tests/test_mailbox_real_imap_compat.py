from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import default

import pytest

from app.auth import AuthSession
from app.mail_adapters import MailAdapterError
from app.mailbox import MessageOperationRequest, operate_messages, search_messages


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
        }
        self.copy_calls: list[tuple[str, str]] = []
        self.store_calls: list[tuple[str, str, str]] = []
        self.expunge_calls = 0
        self.search_calls: list[str] = []

    def list_folders(self) -> list[str]:
        return [
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "Trash"',
            '(\\HasNoChildren) "/" "Archive"',
        ]

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

    page = search_messages(_session(), "INBOX", "release", refresh=True)

    assert adapter.search_calls == ["ALL"]
    assert page.total == 1
    assert [message["uid"] for message in page.messages] == ["1"]


def test_operate_messages_delete_uses_resolved_trash_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = FakeCompatAdapter()
    monkeypatch.setattr("app.mailbox._connect_imap", lambda session: adapter)

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

    payload = MessageOperationRequest(action="move", uids=["2"], target_folder=".Archive")
    result = operate_messages(_session(), "INBOX", payload)

    assert adapter.copy_calls == [("2", "Archive")]
    assert adapter.expunge_calls == 1
    assert "2" not in adapter.messages["INBOX"]
    assert "2" in adapter.messages["Archive"]
    assert result["target_folder"] == "Archive"
