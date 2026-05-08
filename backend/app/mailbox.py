from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.policy import default
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

import bleach
from fastapi import status
from pydantic import BaseModel, Field, model_validator

from app.auth import AuthSession
from app.cache import JsonCache
from app.config import get_settings
from app.errors import AppError
from app import mail_adapters, redis_client
from app.mail_adapters import ImapSettings, MailAdapterError, _parse_status_response
from app.mail_state import ensure_mail_account, persist_message_read_state, sync_folders, sync_message_summaries
from app.security import validate_attachment_id


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
    "b",
    "blockquote",
    "br",
    "code",
    "div",
    "em",
    "font",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
]
ALLOWED_HTML_PROTOCOLS = ["http", "https", "mailto", "data"]
ALLOWED_CSS_PROPERTIES = [
    "background",
    "background-color",
    "border",
    "border-bottom",
    "border-collapse",
    "border-left",
    "border-right",
    "border-top",
    "color",
    "font",
    "font-family",
    "font-size",
    "font-style",
    "font-weight",
    "height",
    "line-height",
    "margin",
    "margin-bottom",
    "margin-left",
    "margin-right",
    "margin-top",
    "max-height",
    "max-width",
    "min-height",
    "min-width",
    "padding",
    "padding-bottom",
    "padding-left",
    "padding-right",
    "padding-top",
    "table-layout",
    "text-align",
    "text-decoration",
    "vertical-align",
    "width",
]


class _SimpleCSSSanitizer:
    def __init__(self, allowed_css_properties: list[str]) -> None:
        self.allowed_css_properties = set(allowed_css_properties)

    def sanitize_css(self, style: str) -> str:
        safe_rules: list[str] = []
        for raw_rule in style.split(";"):
            property_name, separator, raw_value = raw_rule.partition(":")
            if not separator:
                continue
            property_name = property_name.strip().lower()
            value = raw_value.strip()
            lowered_value = value.lower()
            if property_name not in self.allowed_css_properties:
                continue
            if any(token in lowered_value for token in ("expression", "javascript:", "vbscript:", "behavior:", "@import")):
                continue
            if "url(" in lowered_value and "data:image/" not in lowered_value:
                continue
            if value:
                safe_rules.append(f"{property_name}:{value}")
        return ";".join(safe_rules)


def _allowed_html_attr(tag: str, name: str, value: str) -> bool:
    if name in {"class", "style"}:
        return True
    if tag == "a":
        if name in {"title", "target", "rel"}:
            return True
        if name == "href":
            return value.lower().startswith(("http://", "https://", "mailto:"))
        return False
    if tag == "font":
        return name in {"color", "face", "size"}
    if tag == "img":
        if name in {"alt", "title", "width", "height"}:
            return True
        if name == "src":
            return value.lower().startswith(("http://", "https://", "data:image/"))
        return False
    if tag in {"td", "th"}:
        return name in {"colspan", "rowspan"}
    return False


@dataclass(frozen=True)
class MailboxPage:
    folder: str
    page: int
    page_size: int
    total: int
    messages: list[dict[str, Any]]
    cached: bool


class MessageOperationRequest(BaseModel):
    action: str
    uids: list[str] = Field(default_factory=list)
    target_folder: str | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "MessageOperationRequest":
        if not self.uids:
            raise ValueError("至少需要选择一封邮件")
        allowed = {"mark_read", "mark_unread", "delete", "move", "flag", "star", "unflag", "unstar"}
        if self.action not in allowed:
            raise ValueError("不支持的邮件操作")
        if self.action == "move" and not self.target_folder:
            raise ValueError("移动邮件必须指定目标文件夹")
        return self


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
        mailbox_state=getattr(get_settings(), "_mailbox_state", None),
        _mailbox_state=getattr(get_settings(), "_mailbox_state", None),
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


def _system_folder_map(existing: list[str]) -> dict[str, str]:
    return {
        canonical: _resolve_system_folder(existing, canonical, aliases)
        for canonical, _folder_type, _display_name, aliases in SYSTEM_FOLDERS
    }


def _resolved_target_folder(target_folder: str | None, folder_map: dict[str, str]) -> str | None:
    if target_folder is None:
        return None
    return folder_map.get(target_folder, target_folder)


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
        ensure_mail_account(session.email)
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
        sync_folders(session.email, folders)
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


def _safe_filename(value: str) -> str:
    name = value.replace("\\", "/").split("/")[-1].strip()
    name = re.sub(r"[\r\n\x00]+", "", name)
    return name or "attachment"


def _attachment_id(index: int) -> str:
    return f"att_{index}"


def _body_parts(message: Message, *, include_content: bool = False) -> tuple[str, str, list[dict[str, Any]]]:
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
            item = {
                "attachment_id": _attachment_id(len(attachments)),
                "filename": _safe_filename(_decode_header_value(filename or "未命名附件")),
                "content_type": content_type,
                "size_bytes": len(payload),
            }
            if include_content:
                item["content"] = payload
            attachments.append(item)
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
    value = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", "", value)
    clean_options: dict[str, Any] = {
        "tags": ALLOWED_HTML_TAGS,
        "attributes": _allowed_html_attr,
        "protocols": ALLOWED_HTML_PROTOCOLS,
        "strip": True,
    }
    try:
        from bleach.css_sanitizer import CSSSanitizer

        clean_options["css_sanitizer"] = CSSSanitizer(allowed_css_properties=ALLOWED_CSS_PROPERTIES)
    except (ImportError, AttributeError, ModuleNotFoundError):
        clean_options["css_sanitizer"] = _SimpleCSSSanitizer(ALLOWED_CSS_PROPERTIES)
    try:
        cleaned = bleach.clean(
            value,
            **clean_options,
        )
    except TypeError:
        clean_options.pop("css_sanitizer", None)
        cleaned = bleach.clean(
            value,
            **clean_options,
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
    recipients = _addresses(message.get("To"))
    return {
        "uid": str(uid),
        "message_id": message.get("Message-ID"),
        "subject": _decode_header_value(message.get("Subject")) or "(无主题)",
        "sender": sender[0] if sender else {"name": "", "email": ""},
        "to": recipients,
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


def _search_cache_key(email: str, folder: str, query: str, page: int, page_size: int) -> str:
    return f"mail:search:{email}:{folder}:{query}:{page}:{page_size}"


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
        sync_message_summaries(session.email, folder, messages, total_count=len(messages))
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


def search_messages(
    session: AuthSession,
    folder: str,
    query: str,
    *,
    page: int = 1,
    page_size: int = 30,
    refresh: bool = False,
) -> MailboxPage:
    normalized_query = query.strip()
    if not normalized_query:
        raise AppError("SEARCH_QUERY_REQUIRED", "搜索关键词不能为空", http_status=status.HTTP_422_UNPROCESSABLE_CONTENT)

    cache = JsonCache(redis_client.get_redis_client())
    key = _search_cache_key(session.email, folder, normalized_query, page, page_size)
    if not refresh:
        cached = cache.get(key)
        if cached:
            return MailboxPage(cached=True, **cached)

    adapter = _connect_imap(session)
    try:
        adapter.select_folder(folder)
        uids = _uid_search(adapter, "ALL")
        rows = [_message_summary(uid, _uid_fetch_message_bytes(adapter, uid)) for uid in uids]
        sync_message_summaries(session.email, folder, rows, total_count=len(rows))
        lowered = normalized_query.lower()
        messages = [
            row
            for row in rows
            if lowered in str(row.get("subject", "")).lower()
            or lowered in str(row.get("snippet", "")).lower()
            or lowered in str(row.get("sender", {})).lower()
            or lowered in str(row.get("to", [])).lower()
        ]
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
        return MailboxPage(cached=False, **payload)
    except MailAdapterError as exc:
        raise AppError(
            "MAILBOX_SEARCH_FAILED",
            "搜索邮件失败",
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
        if bool(session.preferences.get("mark_read_on_open", True)):
            adapter.mark_seen(uid)
            persist_message_read_state(session.email, folder, [uid], is_read=True)
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


def get_message_attachment(session: AuthSession, folder: str, uid: str, attachment_id: str) -> dict[str, Any]:
    validate_attachment_id(attachment_id)
    adapter = _connect_imap(session)
    try:
        adapter.select_folder(folder)
        raw = _uid_fetch_message_bytes(adapter, uid)
        message = message_from_bytes(raw, policy=default)
        _, _, attachments = _body_parts(message, include_content=True)
        for attachment in attachments:
            if attachment["attachment_id"] == attachment_id:
                return attachment
        raise AppError(
            "ATTACHMENT_NOT_FOUND",
            "附件不存在",
            http_status=status.HTTP_404_NOT_FOUND,
        )
    except AppError:
        raise
    except MailAdapterError as exc:
        raise AppError(
            "ATTACHMENT_DOWNLOAD_FAILED",
            "下载附件失败",
            http_status=status.HTTP_502_BAD_GATEWAY,
            details={"operation": exc.operation},
        ) from exc
    finally:
        adapter.logout()


def operate_messages(session: AuthSession, folder: str, payload: MessageOperationRequest) -> dict[str, Any]:
    adapter = _connect_imap(session)
    try:
        folder_map = _system_folder_map([_folder_name_from_list_line(line) for line in adapter.list_folders()])
        trash_folder = folder_map.get(".Trash", ".Trash")
        target_folder = _resolved_target_folder(payload.target_folder, folder_map)
        adapter.select_folder(folder)
        for uid in payload.uids:
            if payload.action == "mark_read":
                adapter.store_flags(uid, "+FLAGS", "(\\Seen)")
            elif payload.action == "mark_unread":
                adapter.store_flags(uid, "-FLAGS", "(\\Seen)")
            elif payload.action == "delete":
                adapter.copy_message(uid, trash_folder)
                adapter.store_flags(uid, "+FLAGS", "(\\Deleted)")
            elif payload.action == "move":
                adapter.copy_message(uid, str(target_folder))
                adapter.store_flags(uid, "+FLAGS", "(\\Deleted)")
            elif payload.action in {"flag", "star"}:
                adapter.store_flags(uid, "+FLAGS", "(\\Flagged)")
            elif payload.action in {"unflag", "unstar"}:
                adapter.store_flags(uid, "-FLAGS", "(\\Flagged)")
        if payload.action == "mark_read":
            persist_message_read_state(session.email, folder, payload.uids, is_read=True)
        elif payload.action == "mark_unread":
            persist_message_read_state(session.email, folder, payload.uids, is_read=False)
        if payload.action in {"delete", "move"}:
            adapter.expunge()
        return {"action": payload.action, "folder": folder, "target_folder": target_folder, "uids": payload.uids}
    except MailAdapterError as exc:
        raise AppError(
            "MAILBOX_OPERATION_FAILED",
            "邮件操作失败",
            http_status=status.HTTP_502_BAD_GATEWAY,
            details={"operation": exc.operation},
        ) from exc
    finally:
        adapter.logout()
