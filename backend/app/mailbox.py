from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.policy import default
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

import bleach
from fastapi import status

from app.auth import AuthSession
from app.cache import JsonCache
from app.config import get_settings
from app.errors import AppError
from app import mail_adapters, redis_client
from app.mail_adapters import ImapSettings, MailAdapterError, _parse_status_response


SYSTEM_FOLDERS = [
    ("INBOX", "inbox", "收件箱", ("INBOX",)),
    (".Sent", "sent", "已发送", ("SENT", "SENT MESSAGES", "已发送")),
    (".Drafts", "drafts", "草稿箱", ("DRAFTS", "草稿")),
    (".Junk", "spam", "垃圾邮件", ("JUNK", "SPAM", "垃圾")),
    (".Trash", "trash", "已删除", ("TRASH", "DELETED", "已删除")),
    (".Archive", "archive", "归档", ("ARCHIVE", "归档")),
]

ALLOWED_HTML_TAGS = [
    "a",
    "abbr",
    "blockquote",
    "br",
    "code",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
]
ALLOWED_HTML_ATTRS = {"a": ["href", "title", "target", "rel"], "*": ["class"]}
ALLOWED_HTML_PROTOCOLS = ["http", "https", "mailto"]


@dataclass(frozen=True)
class MailboxPage:
    folder: str
    page: int
    page_size: int
    total: int
    messages: list[dict[str, Any]]
    cached: bool


def _imap_settings(session: AuthSession) -> ImapSettings:
    imap_config = session.imap
    return ImapSettings(
        host=str(imap_config.get("host") or get_settings().mail_imap_host),
        port=int(imap_config.get("port") or get_settings().mail_imap_port),
        username=session.email,
        password=session.password,
        use_ssl=bool(imap_config.get("ssl", get_settings().mail_imap_ssl)),
        starttls=bool(imap_config.get("starttls", get_settings().mail_imap_starttls)),
        timeout=15,
    )


def _connect_imap(session: AuthSession) -> ImapAdapter:
    adapter = mail_adapters.ImapAdapter(_imap_settings(session))
    try:
        return adapter.connect().login()
    except MailAdapterError as exc:
        raise AppError(
            "MAILBOX_IMAP_ERROR",
            "连接邮箱服务器失败",
            http_status=status.HTTP_502_BAD_GATEWAY,
            details={"operation": exc.operation},
        ) from exc


def _folder_name_from_list_line(line: str) -> str:
    match = re.search(r'"([^"]+)"\s*$', line.strip())
    if match:
        return match.group(1)
    return line.strip().split()[-1].strip('"')


def _folder_match_score(candidate: str, aliases: tuple[str, ...]) -> int:
    normalized = candidate.strip().strip('"').lstrip(".").upper()
    for alias in aliases:
        alias_normalized = alias.lstrip(".").upper()
        if normalized == alias_normalized:
            return 3
        if normalized.endswith(alias_normalized):
            return 2
        if alias_normalized in normalized:
            return 1
    return 0


def _resolve_system_folder(existing: list[str], canonical: str, aliases: tuple[str, ...]) -> str:
    scored = sorted(
        ((name, _folder_match_score(name, aliases)) for name in existing),
        key=lambda item: item[1],
        reverse=True,
    )
    if scored and scored[0][1] > 0:
        return scored[0][0]
    return canonical


def _status_dict(status_result: Any) -> dict[str, int]:
    if isinstance(status_result, dict):
        return {str(key).upper(): int(value) for key, value in status_result.items()}
    if isinstance(status_result, tuple) and len(status_result) >= 2:
        data = status_result[1]
        if isinstance(data, list) and data:
            raw = data[0]
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            return _parse_status_response(str(raw))
    return {}


def _uid_search(adapter: Any, criteria: str) -> list[str]:
    if hasattr(adapter, "uid_search"):
        return [str(uid) for uid in adapter.uid_search(criteria)]
    return [str(uid) for uid in adapter.search_uids(criteria)]


def _uid_fetch_message_bytes(adapter: Any, uid: str) -> bytes:
    if hasattr(adapter, "uid_fetch_message_bytes"):
        return adapter.uid_fetch_message_bytes(uid)
    return adapter.fetch_message_bytes(uid)


def list_folders(session: AuthSession) -> list[dict[str, Any]]:
    adapter = _connect_imap(session)
    try:
        raw_folders = adapter.list_folders()
        existing = [_folder_name_from_list_line(line) for line in raw_folders]
        folders: list[dict[str, Any]] = []
        for canonical, folder_type, display_name, aliases in SYSTEM_FOLDERS:
            name = _resolve_system_folder(existing, canonical, aliases)
            status_data = _status_dict(adapter.status(name, "(MESSAGES UNSEEN UIDVALIDITY)"))
            folders.append(
                {
                    "name": name,
                    "canonical_name": canonical,
                    "display_name": display_name,
                    "type": folder_type,
                    "delimiter": "/",
                    "unread_count": int(status_data.get("UNSEEN", 0)),
                    "total_count": int(status_data.get("MESSAGES", 0)),
                    "uid_validity": status_data.get("UIDVALIDITY"),
                }
            )
        return folders
    except MailAdapterError as exc:
        raise AppError(
            "MAILBOX_FOLDER_SYNC_FAILED",
            "同步文件夹失败",
            http_status=status.HTTP_502_BAD_GATEWAY,
            details={"operation": exc.operation},
        ) from exc
    finally:
        adapter.logout()


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _addresses(value: str | None) -> list[dict[str, str]]:
    result = []
    for name, email_address in getaddresses([value or ""]):
        if not email_address:
            continue
        result.append({"name": _decode_header_value(name), "email": email_address})
    return result


def _message_datetime(message: Message) -> datetime | None:
    value = message.get("Date")
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _body_parts(message: Message) -> tuple[str, str, list[dict[str, Any]]]:
    html_body = ""
    text_body = ""
    attachments: list[dict[str, Any]] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        content_disposition = (part.get_content_disposition() or "").lower()
        content_type = part.get_content_type()
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename or content_disposition == "attachment":
            attachments.append(
                {
                    "filename": _decode_header_value(filename or "未命名附件"),
                    "content_type": content_type,
                    "size_bytes": len(payload),
                }
            )
            continue
        if content_type not in {"text/html", "text/plain"}:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            decoded = payload.decode(charset, errors="replace")
        except LookupError:
            decoded = payload.decode("utf-8", errors="replace")
        if content_type == "text/html" and not html_body:
            html_body = decoded
        if content_type == "text/plain" and not text_body:
            text_body = decoded
    return html_body, text_body, attachments


def _clean_html(value: str) -> str:
    cleaned = bleach.clean(
        value,
        tags=ALLOWED_HTML_TAGS,
        attributes=ALLOWED_HTML_ATTRS,
        protocols=ALLOWED_HTML_PROTOCOLS,
        strip=True,
    )
    return bleach.linkify(cleaned, callbacks=[_link_attrs])


def _link_attrs(attrs: dict[tuple[str | None, str], str], new: bool = False) -> dict[tuple[str | None, str], str]:
    href_key = (None, "href")
    href = attrs.get(href_key, "")
    if href.lower().startswith("javascript:"):
        attrs.pop(href_key, None)
    attrs[(None, "rel")] = "noopener noreferrer"
    attrs[(None, "target")] = "_blank"
    return attrs


def _text_to_html(value: str) -> str:
    return "<br>".join(html.escape(value).splitlines())


def _snippet(html_body: str, text_body: str) -> str:
    source = text_body or re.sub(r"<[^>]+>", " ", html_body)
    return re.sub(r"\s+", " ", source).strip()[:180]


def _read_flag(message: Message) -> bool:
    flags = " ".join(message.get_all("X-IMAP-Flags", []))
    status_value = message.get("Status", "")
    return "\\Seen" in flags or "R" in status_value


def _message_summary(uid: str, raw: bytes) -> dict[str, Any]:
    message = message_from_bytes(raw, policy=default)
    html_body, text_body, attachments = _body_parts(message)
    sent_at = _message_datetime(message)
    sender = _addresses(message.get("From"))
    return {
        "uid": str(uid),
        "message_id": message.get("Message-ID"),
        "subject": _decode_header_value(message.get("Subject")) or "(无主题)",
        "sender": sender[0] if sender else {"name": "", "email": ""},
        "date": sent_at.isoformat() if sent_at else None,
        "read": _read_flag(message),
        "has_attachments": bool(attachments),
        "snippet": _snippet(html_body, text_body),
    }


def _message_detail(uid: str, raw: bytes) -> dict[str, Any]:
    message = message_from_bytes(raw, policy=default)
    html_body, text_body, attachments = _body_parts(message)
    safe_html = _clean_html(html_body) if html_body else _text_to_html(text_body)
    sent_at = _message_datetime(message)
    return {
        "uid": str(uid),
        "message_id": message.get("Message-ID"),
        "subject": _decode_header_value(message.get("Subject")) or "(无主题)",
        "from": _addresses(message.get("From")),
        "to": _addresses(message.get("To")),
        "cc": _addresses(message.get("Cc")),
        "date": sent_at.isoformat() if sent_at else None,
        "html_body": safe_html,
        "text_body": text_body,
        "read": True,
        "attachments": attachments,
    }


def _message_cache_key(email: str, folder: str, page: int, page_size: int) -> str:
    return f"mail:list:{email}:{folder}:{page}:{page_size}"


def list_messages(
    session: AuthSession,
    folder: str,
    *,
    page: int = 1,
    page_size: int = 30,
    refresh: bool = False,
) -> MailboxPage:
    cache = JsonCache(redis_client.get_redis_client())
    key = _message_cache_key(session.email, folder, page, page_size)
    if not refresh:
        cached = cache.get(key)
        if cached:
            return MailboxPage(cached=True, **cached)

    adapter = _connect_imap(session)
    try:
        adapter.select_folder(folder)
        uids = _uid_search(adapter, "ALL")
        messages = [_message_summary(uid, _uid_fetch_message_bytes(adapter, uid)) for uid in uids]
        messages.sort(key=lambda item: item["date"] or "", reverse=True)
        offset = max(page - 1, 0) * page_size
        page_messages = messages[offset : offset + page_size]
        payload = {
            "folder": folder,
            "page": page,
            "page_size": page_size,
            "total": len(messages),
            "messages": page_messages,
        }
        cache.set(key, payload, ttl_seconds=60)
        return MailboxPage(cached=False, **payload)
    except MailAdapterError as exc:
        raise AppError(
            "MAILBOX_MESSAGE_LIST_FAILED",
            "获取邮件列表失败",
            http_status=status.HTTP_502_BAD_GATEWAY,
            details={"operation": exc.operation},
        ) from exc
    finally:
        adapter.logout()


def get_message_detail(session: AuthSession, folder: str, uid: str) -> dict[str, Any]:
    adapter = _connect_imap(session)
    try:
        adapter.select_folder(folder)
        raw = _uid_fetch_message_bytes(adapter, uid)
        detail = _message_detail(uid, raw)
        adapter.mark_seen(uid)
        detail["read"] = True
        return detail
    except MailAdapterError as exc:
        raise AppError(
            "MAILBOX_MESSAGE_DETAIL_FAILED",
            "获取邮件详情失败",
            http_status=status.HTTP_502_BAD_GATEWAY,
            details={"operation": exc.operation},
        ) from exc
    finally:
        adapter.logout()
