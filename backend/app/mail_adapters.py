from __future__ import annotations

import imaplib
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Iterable


class MailAdapterError(Exception):
    def __init__(self, message: str, *, operation: str | None = None) -> None:
        self.operation = operation
        super().__init__(message)


@dataclass(slots=True)
class ImapSettings:
    host: str
    username: str
    password: str
    port: int | None = None
    use_ssl: bool = False
    starttls: bool = False
    timeout: float | None = None
    ssl_context: ssl.SSLContext | None = None
    mailbox_state: Any | None = None
    _mailbox_state: Any | None = None


@dataclass(slots=True)
class SmtpSettings:
    host: str
    username: str
    password: str
    port: int | None = None
    use_ssl: bool = False
    starttls: bool = False
    timeout: float | None = None
    ssl_context: ssl.SSLContext | None = None


def _format_error(operation: str, exc: Exception) -> MailAdapterError:
    return MailAdapterError(f"{operation}失败: {exc}", operation=operation)


def _decode_text(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _search_criteria_args(criteria: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(criteria, str):
        return (criteria,)
    return tuple(str(item) for item in criteria)


def _parse_status_response(raw: str) -> dict[str, int]:
    if "(" in raw and ")" in raw:
        raw = raw.split("(", 1)[1].rsplit(")", 1)[0]
    parts = raw.replace("\r", " ").replace("\n", " ").split()
    result: dict[str, int] = {}
    for index in range(0, len(parts) - 1, 2):
        key = parts[index].upper()
        try:
            result[key] = int(parts[index + 1])
        except ValueError:
            continue
    return result


class ImapAdapter:
    def __init__(self, settings: ImapSettings) -> None:
        self.settings = settings
        self._client: Any | None = None

    def _default_port(self) -> int:
        if self.settings.port is not None:
            return self.settings.port
        return 993 if self.settings.use_ssl else 143

    def _ensure_client(self) -> Any:
        if self._client is None:
            self.connect()
        if self._client is None:
            raise MailAdapterError("IMAP 客户端未初始化", operation="connect")
        return self._client

    def connect(self) -> "ImapAdapter":
        if self._client is not None:
            return self
        try:
            if self.settings.use_ssl and self.settings.starttls:
                raise MailAdapterError("IMAP SSL 与 STARTTLS 不能同时启用", operation="connect")
            if self.settings.use_ssl:
                self._client = imaplib.IMAP4_SSL(
                    self.settings.host,
                    self._default_port(),
                    ssl_context=self.settings.ssl_context,
                    timeout=self.settings.timeout,
                )
            else:
                self._client = imaplib.IMAP4(
                    self.settings.host,
                    self._default_port(),
                    timeout=self.settings.timeout,
                )
                if self.settings.starttls:
                    self._client.starttls(ssl_context=self.settings.ssl_context)
            return self
        except MailAdapterError:
            self._client = None
            raise
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            self._client = None
            raise _format_error("IMAP 连接", exc) from exc

    def login(self) -> "ImapAdapter":
        client = self._ensure_client()
        try:
            client.login(self.settings.username, self.settings.password)
            return self
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP 登录", exc) from exc

    def logout(self) -> None:
        if self._client is None:
            return
        try:
            self._client.logout()
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP 登出", exc) from exc
        finally:
            self._client = None

    def capability(self) -> list[str]:
        client = self._ensure_client()
        try:
            status, data = client.capability()
            if status != "OK":
                raise MailAdapterError(f"IMAP 能力查询失败: {status}", operation="capability")
            raw = data[0] if data else b""
            return [item for item in _decode_text(raw).split() if item]
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP 能力查询", exc) from exc

    def list_folders(self) -> list[str]:
        client = self._ensure_client()
        try:
            status, data = client.list()
            if status != "OK":
                raise MailAdapterError(f"IMAP 文件夹列表查询失败: {status}", operation="list_folders")
            return [_decode_text(item) for item in data if item]
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP 文件夹列表查询", exc) from exc

    def select_folder(self, folder: str) -> tuple[str, list[bytes]]:
        client = self._ensure_client()
        try:
            return client.select(folder)
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP 选择文件夹", exc) from exc

    def status(self, folder: str, items: str = "(MESSAGES UNSEEN UIDVALIDITY)") -> dict[str, int]:
        client = self._ensure_client()
        try:
            status, data = client.status(folder, items)
            if status != "OK":
                raise MailAdapterError(f"IMAP 状态查询失败: {status}", operation="status")
            raw = _decode_text(data[0] if data else "")
            return _parse_status_response(raw)
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP 状态查询", exc) from exc

    def search_uids(self, criteria: str | Iterable[str]) -> list[str]:
        client = self._ensure_client()
        try:
            status, data = client.search(None, *_search_criteria_args(criteria))
            if status != "OK":
                raise MailAdapterError(f"IMAP 搜索失败: {status}", operation="search_uids")
            if not data:
                return []
            raw = data[0]
            return [part for part in _decode_text(raw).split() if part]
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP 搜索", exc) from exc

    def uid_search(self, criteria: str | Iterable[str]) -> list[str]:
        client = self._ensure_client()
        try:
            status, data = client.uid("SEARCH", None, *_search_criteria_args(criteria))
            if status != "OK":
                raise MailAdapterError(f"IMAP UID 搜索失败: {status}", operation="uid_search")
            if not data:
                return []
            return [part for part in _decode_text(data[0]).split() if part]
        except AttributeError:
            return self.search_uids(criteria)
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP UID 搜索", exc) from exc

    def fetch_message_bytes(self, uid: str | bytes) -> bytes:
        client = self._ensure_client()
        try:
            status, data = client.fetch(uid, "(RFC822)")
            if status != "OK":
                raise MailAdapterError(f"IMAP 获取邮件失败: {status}", operation="fetch_message_bytes")
            for item in data or []:
                if isinstance(item, tuple) and len(item) >= 2:
                    payload = item[1]
                    if isinstance(payload, bytes):
                        return payload
                    if isinstance(payload, str):
                        return payload.encode("utf-8")
            raise MailAdapterError("IMAP 邮件内容为空", operation="fetch_message_bytes")
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP 获取邮件", exc) from exc

    def uid_fetch_message_bytes(self, uid: str | bytes) -> bytes:
        client = self._ensure_client()
        try:
            status, data = client.uid("FETCH", uid, "(RFC822)")
            if status != "OK":
                raise MailAdapterError(f"IMAP UID 获取邮件失败: {status}", operation="uid_fetch_message_bytes")
            for item in data or []:
                if isinstance(item, tuple) and len(item) >= 2:
                    payload = item[1]
                    if isinstance(payload, bytes):
                        return payload
                    if isinstance(payload, str):
                        return payload.encode("utf-8")
            raise MailAdapterError("IMAP UID 邮件内容为空", operation="uid_fetch_message_bytes")
        except AttributeError:
            return self.fetch_message_bytes(uid)
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP UID 获取邮件", exc) from exc

    def mark_seen(self, uid: str | bytes) -> None:
        client = self._ensure_client()
        try:
            if hasattr(client, "uid"):
                status, _ = client.uid("STORE", uid, "+FLAGS", "(\\Seen)")
            else:
                status, _ = client.store(uid, "+FLAGS", "\\Seen")
            if status != "OK":
                raise MailAdapterError(f"IMAP 标记已读失败: {status}", operation="mark_seen")
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP 标记已读", exc) from exc

    def store_flags(self, uid: str | bytes, command: str, flags: str) -> None:
        client = self._ensure_client()
        try:
            if hasattr(client, "uid"):
                status, _ = client.uid("STORE", uid, command, flags)
            else:
                status, _ = client.store(uid, command, flags)
            if status != "OK":
                raise MailAdapterError(f"IMAP Flag 操作失败: {status}", operation="store_flags")
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP Flag 操作", exc) from exc

    def copy_message(self, uid: str | bytes, target_folder: str) -> None:
        client = self._ensure_client()
        try:
            if hasattr(client, "uid"):
                status, _ = client.uid("COPY", uid, target_folder)
            else:
                status, _ = client.copy(uid, target_folder)
            if status != "OK":
                raise MailAdapterError(f"IMAP 复制邮件失败: {status}", operation="copy_message")
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP 复制邮件", exc) from exc

    def expunge(self) -> None:
        client = self._ensure_client()
        try:
            client.expunge()
        except AttributeError:
            return
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP 清理删除邮件", exc) from exc

    def delete_message(self, folder: str, draft_id: str) -> int:
        self.select_folder(folder)
        uids = self.uid_search(["HEADER", "X-Draft-ID", draft_id])
        for uid in uids:
            self.store_flags(uid, "+FLAGS", "(\\Deleted)")
        if uids:
            self.expunge()
        return len(uids)

    def append_message(self, folder: str, message: EmailMessage) -> None:
        client = self._ensure_client()
        try:
            client.append(folder, None, None, message.as_bytes())
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP 追加邮件", exc) from exc


class SmtpAdapter:
    def __init__(self, settings: SmtpSettings) -> None:
        self.settings = settings
        self._client: Any | None = None

    def _default_port(self) -> int:
        if self.settings.port is not None:
            return self.settings.port
        if self.settings.use_ssl:
            return 465
        if self.settings.starttls:
            return 587
        return 25

    def _ensure_client(self) -> Any:
        if self._client is None:
            self.connect()
        if self._client is None:
            raise MailAdapterError("SMTP 客户端未初始化", operation="connect")
        return self._client

    def connect(self) -> "SmtpAdapter":
        if self._client is not None:
            return self
        try:
            if self.settings.use_ssl and self.settings.starttls:
                raise MailAdapterError("SMTP SSL 与 STARTTLS 不能同时启用", operation="connect")
            if self.settings.use_ssl:
                self._client = smtplib.SMTP_SSL(
                    self.settings.host,
                    self._default_port(),
                    timeout=self.settings.timeout,
                    context=self.settings.ssl_context,
                )
            else:
                self._client = smtplib.SMTP(
                    self.settings.host,
                    self._default_port(),
                    timeout=self.settings.timeout,
                )
                if self.settings.starttls:
                    self._client.ehlo()
                    self._client.starttls(context=self.settings.ssl_context)
                    self._client.ehlo()
            return self
        except MailAdapterError:
            self._client = None
            raise
        except (
            OSError,
            ssl.SSLError,
            smtplib.SMTPException,
        ) as exc:
            self._client = None
            raise _format_error("SMTP 连接", exc) from exc

    def login(self) -> "SmtpAdapter":
        client = self._ensure_client()
        try:
            client.login(self.settings.username, self.settings.password)
            return self
        except (
            OSError,
            ssl.SSLError,
            smtplib.SMTPException,
        ) as exc:
            raise _format_error("SMTP 登录", exc) from exc

    def send_message(self, message: EmailMessage) -> Any:
        client = self._ensure_client()
        try:
            return client.send_message(message)
        except (
            OSError,
            ssl.SSLError,
            smtplib.SMTPException,
        ) as exc:
            raise _format_error("SMTP 发送邮件", exc) from exc

    def ehlo_features(self) -> dict[str, str]:
        client = self._ensure_client()
        try:
            client.ehlo()
            return dict(client.esmtp_features)
        except (
            OSError,
            ssl.SSLError,
            smtplib.SMTPException,
        ) as exc:
            raise _format_error("SMTP EHLO", exc) from exc

    def quit(self) -> None:
        if self._client is None:
            return
        try:
            self._client.quit()
        except (
            OSError,
            ssl.SSLError,
            smtplib.SMTPException,
        ) as exc:
            raise _format_error("SMTP 退出", exc) from exc
        finally:
            self._client = None
