"""邮件协议适配层。

这个模块把底层的 IMAP/SMTP 标准库封装成统一接口，供认证、收信、
发信、草稿等业务模块复用。
"""

from __future__ import annotations

import base64
import imaplib
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Iterable


class MailAdapterError(Exception):
    """统一的邮件适配器异常，附带失败操作名。"""

    def __init__(self, message: str, *, operation: str | None = None) -> None:
        self.operation = operation
        super().__init__(message)


@dataclass(slots=True)
class ImapSettings:
    """IMAP 连接参数。"""

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
    """SMTP 连接参数。"""

    host: str
    username: str
    password: str
    port: int | None = None
    use_ssl: bool = False
    starttls: bool = False
    timeout: float | None = None
    ssl_context: ssl.SSLContext | None = None


def _format_error(operation: str, exc: Exception) -> MailAdapterError:
    """把底层异常包装成项目内统一异常。"""
    return MailAdapterError(f"{operation}失败: {exc}", operation=operation)


def _decode_text(value: bytes | str) -> str:
    """把 bytes/str 统一转换为字符串。"""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _extract_fetch_payload(data: Any, *, operation: str) -> bytes:
    """从 IMAP FETCH 返回值中提取首个字节负载。"""
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2:
            payload = item[1]
            if isinstance(payload, bytes):
                return payload
            if isinstance(payload, str):
                return payload.encode("utf-8")
    raise MailAdapterError("IMAP 邮件内容为空", operation=operation)


def _search_criteria_args(criteria: str | Iterable[str]) -> tuple[str, ...]:
    """把搜索条件规范为 IMAP 可接受的参数元组。"""
    if isinstance(criteria, str):
        return (criteria,)
    return tuple(str(item) for item in criteria)


def _parse_status_response(raw: str) -> dict[str, int]:
    """解析 IMAP STATUS 原始响应。"""
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


def _encode_modified_utf7(value: str) -> str:
    """把文件夹名编码为 IMAP Modified UTF-7。"""
    if not value:
        return value
    chunks: list[str] = []
    unicode_buffer: list[str] = []

    def flush_unicode_buffer() -> None:
        if not unicode_buffer:
            return
        utf16_bytes = ''.join(unicode_buffer).encode("utf-16-be")
        encoded = base64.b64encode(utf16_bytes).decode("ascii").rstrip("=").replace("/", ",")
        chunks.append(f"&{encoded}-")
        unicode_buffer.clear()

    for char in value:
        if 0x20 <= ord(char) <= 0x7E:
            flush_unicode_buffer()
            chunks.append("&-" if char == "&" else char)
        else:
            unicode_buffer.append(char)

    flush_unicode_buffer()
    return "".join(chunks)


def _decode_modified_utf7(value: str) -> str:
    """把 IMAP Modified UTF-7 解码成人类可读字符串。"""
    if "&" not in value:
        return value

    chunks: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "&":
            chunks.append(value[index])
            index += 1
            continue
        end_index = value.find("-", index)
        if end_index == -1:
            chunks.append(value[index:])
            break
        token = value[index + 1:end_index]
        if token == "":
            chunks.append("&")
        else:
            padding = "=" * ((4 - len(token) % 4) % 4)
            decoded = base64.b64decode(token.replace(",", "/") + padding)
            chunks.append(decoded.decode("utf-16-be"))
        index = end_index + 1
    return "".join(chunks)


def _encode_mailbox_name(value: str) -> str:
    """在发送 IMAP 命令前编码文件夹名称。"""
    if any(ord(char) > 0x7F or char == "&" for char in value):
        return _encode_modified_utf7(value)
    return value


class ImapAdapter:
    """对 ``imaplib`` 的轻量封装。"""

    def __init__(self, settings: ImapSettings) -> None:
        self.settings = settings
        self._client: Any | None = None

    def _default_port(self) -> int:
        """根据连接模式计算默认端口。"""
        if self.settings.port is not None:
            return self.settings.port
        return 993 if self.settings.use_ssl else 143

    def _ensure_client(self) -> Any:
        """确保底层 IMAP 客户端已初始化。"""
        if self._client is None:
            self.connect()
        if self._client is None:
            raise MailAdapterError("IMAP 客户端未初始化", operation="connect")
        return self._client

    def connect(self) -> "ImapAdapter":
        """建立 IMAP 连接。"""
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
        """执行 IMAP 登录。"""
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
        """关闭 IMAP 会话。"""
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
        """查询服务器支持的 IMAP 能力。"""
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
        """列出邮箱中的全部文件夹。"""
        client = self._ensure_client()
        try:
            status, data = client.list()
            if status != "OK":
                raise MailAdapterError(f"IMAP 文件夹列表查询失败: {status}", operation="list_folders")
            return [_decode_modified_utf7(_decode_text(item)) for item in data if item]
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
            return client.select(_encode_mailbox_name(folder))
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
            status, data = client.status(_encode_mailbox_name(folder), items)
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
            return _extract_fetch_payload(data, operation="uid_fetch_message_bytes")
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

    def uid_fetch_headers(self, uid: str | bytes) -> bytes:
        """仅拉取 RFC822 头部。"""
        client = self._ensure_client()
        try:
            status, data = client.uid("FETCH", uid, "(BODY.PEEK[HEADER])")
            if status != "OK":
                raise MailAdapterError(f"IMAP UID 获取邮件头失败: {status}", operation="uid_fetch_headers")
            return _extract_fetch_payload(data, operation="uid_fetch_headers")
        except AttributeError:
            return self.uid_fetch_message_bytes(uid)
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP UID 获取邮件头", exc) from exc

    def uid_fetch_body_section(self, uid: str | bytes, section: str) -> bytes:
        """拉取指定 BODY section 内容。"""
        client = self._ensure_client()
        query = f"(BODY.PEEK[{section}])"
        try:
            status, data = client.uid("FETCH", uid, query)
            if status != "OK":
                raise MailAdapterError(f"IMAP UID 获取正文分段失败: {status}", operation="uid_fetch_body_section")
            return _extract_fetch_payload(data, operation="uid_fetch_body_section")
        except AttributeError:
            return self.uid_fetch_message_bytes(uid)
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP UID 获取正文分段", exc) from exc

    def uid_fetch_bodystructure(self, uid: str | bytes) -> bytes:
        """拉取 BODYSTRUCTURE 响应原文。"""
        client = self._ensure_client()
        try:
            status, data = client.uid("FETCH", uid, "(BODYSTRUCTURE)")
            if status != "OK":
                raise MailAdapterError(f"IMAP UID 获取 BODYSTRUCTURE 失败: {status}", operation="uid_fetch_bodystructure")
            raw = " ".join(_decode_text(item) if not isinstance(item, tuple) else _decode_text(item[0]) for item in (data or []))
            if not raw.strip():
                raise MailAdapterError("IMAP BODYSTRUCTURE 为空", operation="uid_fetch_bodystructure")
            return raw.encode("utf-8", errors="replace")
        except AttributeError:
            return b""
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP UID 获取 BODYSTRUCTURE", exc) from exc

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
                status, _ = client.uid("COPY", uid, _encode_mailbox_name(target_folder))
            else:
                status, _ = client.copy(uid, _encode_mailbox_name(target_folder))
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

    def create_folder(self, folder: str) -> None:
        client = self._ensure_client()
        try:
            status, _ = client.create(_encode_mailbox_name(folder))
            if status != "OK":
                raise MailAdapterError(f"IMAP 创建文件夹失败: {status}", operation="create_folder")
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP 创建文件夹", exc) from exc

    def rename_folder(self, old_folder: str, new_folder: str) -> None:
        client = self._ensure_client()
        try:
            status, _ = client.rename(_encode_mailbox_name(old_folder), _encode_mailbox_name(new_folder))
            if status != "OK":
                raise MailAdapterError(f"IMAP 重命名文件夹失败: {status}", operation="rename_folder")
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP 重命名文件夹", exc) from exc

    def delete_folder(self, folder: str) -> None:
        client = self._ensure_client()
        try:
            status, _ = client.delete(_encode_mailbox_name(folder))
            if status != "OK":
                raise MailAdapterError(f"IMAP 删除文件夹失败: {status}", operation="delete_folder")
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP 删除文件夹", exc) from exc

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
            client.append(_encode_mailbox_name(folder), None, None, message.as_bytes())
        except (
            OSError,
            ssl.SSLError,
            imaplib.IMAP4.error,
            imaplib.IMAP4.abort,
            imaplib.IMAP4.readonly,
        ) as exc:
            raise _format_error("IMAP 追加邮件", exc) from exc


class SmtpAdapter:
    """对 `smtplib` 的轻量封装，统一 SMTP 连接、登录和发信异常。"""

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
