from __future__ import annotations

import imaplib
import smtplib

import pytest
from email.message import EmailMessage

from app.mail_adapters import (
    ImapAdapter,
    ImapSettings,
    MailAdapterError,
    SmtpAdapter,
    SmtpSettings,
)


class FakeImapProtocolError(RuntimeError):
    pass


def make_imap_fake(bucket: list[object], label: str, *, error_on: str | None = None):
    class FakeImap:
        error = FakeImapProtocolError
        abort = FakeImapProtocolError
        readonly = FakeImapProtocolError

        def __init__(self, host: str = "", port: int = 143, timeout: float | None = None, **kwargs):
            self.label = label
            self.host = host
            self.port = port
            self.timeout = timeout
            self.kwargs = kwargs
            self.calls: list[tuple[object, ...]] = []
            bucket.append(self)
            if error_on == "init":
                raise OSError("imap init failed")

        def starttls(self, ssl_context=None):
            self.calls.append(("starttls", ssl_context))
            if error_on == "starttls":
                raise FakeImapProtocolError("starttls failed")
            return "OK", [b"STARTTLS"]

        def login(self, username: str, password: str):
            self.calls.append(("login", username, password))
            if error_on == "login":
                raise FakeImapProtocolError("login failed")
            return "OK", [b"AUTHENTICATED"]

        def logout(self):
            self.calls.append(("logout",))
            return "BYE", [b"LOGOUT"]

        def capability(self):
            self.calls.append(("capability",))
            return "OK", [b"IMAP4rev1 IDLE UTF8=ACCEPT"]

        def list(self):
            self.calls.append(("list",))
            return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Archive"']

        def select(self, folder: str):
            self.calls.append(("select", folder))
            return "OK", [b"2"]

        def search(self, charset, *criteria):
            self.calls.append(("search", charset, *criteria))
            return "OK", [b"101 102"]

        def fetch(self, uid, query):
            self.calls.append(("fetch", uid, query))
            return "OK", [(b"1 (RFC822 {11})", b"hello world")]

        def append(self, folder, flags, date_time, message):
            self.calls.append(("append", folder, flags, date_time, message))
            if error_on == "append":
                raise FakeImapProtocolError("append failed")
            return "OK", [b"APPENDUID 1 9"]

        def create(self, folder):
            self.calls.append(("create", folder))
            return "OK", [b"CREATE"]

        def rename(self, old_folder, new_folder):
            self.calls.append(("rename", old_folder, new_folder))
            return "OK", [b"RENAME"]

        def delete(self, folder):
            self.calls.append(("delete", folder))
            return "OK", [b"DELETE"]

    FakeImap.__name__ = f"FakeImap{label}"
    return FakeImap


class FakeSmtpProtocolError(RuntimeError):
    pass


def make_smtp_fake(bucket: list[object], label: str, *, error_on: str | None = None):
    class FakeSmtp:
        def __init__(self, host: str = "", port: int = 0, timeout: float | None = None, **kwargs):
            self.label = label
            self.host = host
            self.port = port
            self.timeout = timeout
            self.kwargs = kwargs
            self.calls: list[tuple[object, ...]] = []
            self.esmtp_features: dict[str, str] = {"size": "10485760"}
            bucket.append(self)
            if error_on == "init":
                raise OSError("smtp init failed")

        def ehlo(self):
            self.calls.append(("ehlo",))
            if error_on == "ehlo":
                raise smtplib.SMTPException("ehlo failed")
            self.esmtp_features = {"size": "10485760", "starttls": ""}
            return 250, b"OK"

        def starttls(self, context=None):
            self.calls.append(("starttls", context))
            if error_on == "starttls":
                raise FakeSmtpProtocolError("starttls failed")
            self.esmtp_features = {"size": "10485760"}
            return 220, b"READY"

        def login(self, username: str, password: str):
            self.calls.append(("login", username, password))
            if error_on == "login":
                raise smtplib.SMTPAuthenticationError(535, b"auth failed")
            return 235, b"AUTHENTICATED"

        def send_message(self, message: EmailMessage):
            self.calls.append(("send_message", message))
            if error_on == "send_message":
                raise smtplib.SMTPException("send failed")
            return {"recipient@example.com": (250, b"queued")}

        def quit(self):
            self.calls.append(("quit",))
            return 221, b"BYE"

    FakeSmtp.__name__ = f"FakeSmtp{label}"
    return FakeSmtp


def test_imap_connect_branches_and_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    plain_bucket: list[object] = []
    ssl_bucket: list[object] = []
    plain_cls = make_imap_fake(plain_bucket, "Plain")
    ssl_cls = make_imap_fake(ssl_bucket, "Ssl")
    monkeypatch.setattr("app.mail_adapters.imaplib.IMAP4", plain_cls)
    monkeypatch.setattr("app.mail_adapters.imaplib.IMAP4_SSL", ssl_cls)

    settings = ImapSettings(
        host="imap.example.com",
        username="user@example.com",
        password="secret",
        starttls=True,
        ssl_context=object(),
    )
    adapter = ImapAdapter(settings)
    assert adapter.connect() is adapter
    assert adapter.login() is adapter

    client = plain_bucket[0]
    assert client.host == "imap.example.com"
    assert client.port == 143
    assert client.timeout is None
    assert client.calls[:2] == [("starttls", settings.ssl_context), ("login", "user@example.com", "secret")]

    assert adapter.capability() == ["IMAP4rev1", "IDLE", "UTF8=ACCEPT"]
    assert adapter.list_folders() == ['(\\HasNoChildren) "/" "INBOX"', '(\\HasNoChildren) "/" "Archive"']
    assert adapter.select_folder("INBOX") == ("OK", [b"2"])
    assert adapter.search_uids(["FROM", "alice@example.com"]) == ["101", "102"]
    assert adapter.fetch_message_bytes("101") == b"hello world"

    message = EmailMessage()
    message["Subject"] = "Hello"
    message["From"] = "user@example.com"
    message["To"] = "recipient@example.com"
    message.set_content("body")

    adapter.append_message("INBOX", message)
    append_call = client.calls[-1]
    assert append_call[0] == "append"
    assert append_call[1] == "INBOX"
    assert append_call[2] is None
    assert append_call[3] is None
    assert isinstance(append_call[4], bytes)
    assert b"Subject: Hello" in append_call[4]

    adapter.create_folder("客户归档")
    adapter.rename_folder("客户归档", "客户归档-2026")
    adapter.delete_folder("客户归档-2026")
    assert ("create", "&W6JiN19SaGM-") in client.calls
    assert ("rename", "&W6JiN19SaGM-", "&W6JiN19SaGM--2026") in client.calls
    assert ("delete", "&W6JiN19SaGM--2026") in client.calls

    adapter.logout()
    assert client.calls[-1] == ("logout",)
    assert adapter._client is None

    ssl_adapter = ImapAdapter(
        ImapSettings(
            host="imap.example.com",
            username="user@example.com",
            password="secret",
            use_ssl=True,
        )
    )
    ssl_adapter.connect()
    ssl_client = ssl_bucket[0]
    assert ssl_client.port == 993
    assert ssl_client.calls == []


def test_imap_errors_are_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    plain_bucket: list[object] = []
    plain_cls = make_imap_fake(plain_bucket, "Plain", error_on="init")
    monkeypatch.setattr("app.mail_adapters.imaplib.IMAP4", plain_cls)

    adapter = ImapAdapter(
        ImapSettings(
            host="imap.example.com",
            username="user@example.com",
            password="secret",
        )
    )

    with pytest.raises(MailAdapterError) as exc_info:
        adapter.connect()

    assert "IMAP 连接" in str(exc_info.value)
    assert adapter._client is None


def test_imap_login_errors_are_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    plain_bucket: list[object] = []
    plain_cls = make_imap_fake(plain_bucket, "Plain", error_on="login")
    monkeypatch.setattr("app.mail_adapters.imaplib.IMAP4", plain_cls)

    adapter = ImapAdapter(
        ImapSettings(
            host="imap.example.com",
            username="user@example.com",
            password="secret",
        )
    )
    adapter.connect()

    with pytest.raises(MailAdapterError) as exc_info:
        adapter.login()

    assert "IMAP 登录" in str(exc_info.value)


def test_imap_list_folders_decodes_modified_utf7(monkeypatch: pytest.MonkeyPatch) -> None:
    bucket: list[object] = []
    fake_cls = make_imap_fake(bucket, "Plain")
    monkeypatch.setattr("app.mail_adapters.imaplib.IMAP4", fake_cls)

    adapter = ImapAdapter(
        ImapSettings(
            host="imap.example.com",
            username="user@example.com",
            password="secret",
        )
    )
    adapter.connect()
    client = bucket[0]
    client.list = lambda: ("OK", [b'(\\HasNoChildren) "/" "&W6JiNw-"'])

    assert adapter.list_folders() == ['(\\HasNoChildren) "/" "客户"']


def test_smtp_connect_branches_and_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    plain_bucket: list[object] = []
    ssl_bucket: list[object] = []
    plain_cls = make_smtp_fake(plain_bucket, "Plain")
    ssl_cls = make_smtp_fake(ssl_bucket, "Ssl")
    monkeypatch.setattr("app.mail_adapters.smtplib.SMTP", plain_cls)
    monkeypatch.setattr("app.mail_adapters.smtplib.SMTP_SSL", ssl_cls)

    settings = SmtpSettings(
        host="smtp.example.com",
        username="user@example.com",
        password="secret",
        starttls=True,
        ssl_context=object(),
    )
    adapter = SmtpAdapter(settings)
    assert adapter.connect() is adapter
    assert adapter.login() is adapter

    client = plain_bucket[0]
    assert client.host == "smtp.example.com"
    assert client.port == 587
    assert client.calls[:4] == [
        ("ehlo",),
        ("starttls", settings.ssl_context),
        ("ehlo",),
        ("login", "user@example.com", "secret"),
    ]

    message = EmailMessage()
    message["Subject"] = "Hello"
    message["From"] = "user@example.com"
    message["To"] = "recipient@example.com"
    message.set_content("body")

    assert adapter.send_message(message) == {"recipient@example.com": (250, b"queued")}
    assert adapter.ehlo_features() == {"size": "10485760", "starttls": ""}
    adapter.quit()
    assert client.calls[-1] == ("quit",)
    assert adapter._client is None

    ssl_adapter = SmtpAdapter(
        SmtpSettings(
            host="smtp.example.com",
            username="user@example.com",
            password="secret",
            use_ssl=True,
        )
    )
    ssl_adapter.connect()
    ssl_client = ssl_bucket[0]
    assert ssl_client.port == 465
    assert ssl_client.calls == []


def test_smtp_errors_are_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    plain_bucket: list[object] = []
    plain_cls = make_smtp_fake(plain_bucket, "Plain", error_on="send_message")
    monkeypatch.setattr("app.mail_adapters.smtplib.SMTP", plain_cls)

    adapter = SmtpAdapter(
        SmtpSettings(
            host="smtp.example.com",
            username="user@example.com",
            password="secret",
        )
    )
    adapter.connect()

    message = EmailMessage()
    message["Subject"] = "Broken"
    message["From"] = "user@example.com"
    message["To"] = "recipient@example.com"
    message.set_content("body")

    with pytest.raises(MailAdapterError) as exc_info:
        adapter.send_message(message)

    assert "SMTP 发送邮件" in str(exc_info.value)
