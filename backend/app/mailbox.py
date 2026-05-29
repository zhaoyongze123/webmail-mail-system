"""收信、读信、搜索与邮件操作核心逻辑。

这个模块负责 IMAP 文件夹映射、邮件正文与附件解析、富文本安全清洗、
列表/详情缓存，以及邮件批量操作。
"""

from __future__ import annotations

import html
import io
import json
import hashlib
import os
import re
import zipfile
from base64 import b64decode, b64encode
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email import message_from_bytes
from email.parser import BytesHeaderParser
from email.header import decode_header, make_header
from email.message import Message
from email.policy import default
from email.utils import getaddresses, parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any
from xml.etree import ElementTree
import quopri

import bleach
import tinycss2
from fastapi import status
from premailer import transform
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select
try:
    import fitz
except ModuleNotFoundError:  # pragma: no cover - 运行环境缺依赖时走能力降级
    fitz = None

from app.auth import AuthSession
from app.cache import JsonCache
from app.config import get_settings
from app.contacts import list_blacklisted_contacts, list_whitelisted_contacts
from app.errors import AppError
from app import mail_adapters, redis_client
from app.mail_adapters import ImapSettings, MailAdapterError, _parse_status_response
from app.db import get_session_factory
from app.models import MailAccount, MailAttachmentPreview, MailFolder, MailMessage
from app.mail_state import ensure_mail_account, persist_message_read_state, sync_folders, sync_message_summaries
from app.security import validate_attachment_id


MAILBOX_BACKGROUND_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mailbox-bg")
DETAIL_PREWARM_LIMIT = 5
ATTACHMENT_PREWARM_LIMIT = 3
PREVIEW_CACHE_LOCK = Lock()
PREVIEW_TASK_LOCK = Lock()
PREVIEW_RUNNING_TASKS: set[str] = set()
PREVIEW_HOUSEKEEPING_LOCK = Lock()
PREVIEW_HOUSEKEEPING_STATE = {"last_run_ts": 0.0, "running": False}
ATTACHMENT_THUMBNAIL_CACHE_VERSION = "2026-05-26-thumbnail-v2"


def _is_test_runtime() -> bool:
    """判断当前是否运行在测试进程中，用于关闭非确定性后台预热。"""
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or get_settings().app_env == "test"


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
    "strike",
    "sub",
    "sup",
    "del",
    "ins",
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
    """对白名单 CSS 属性做最小化清洗。"""

    def __init__(self, allowed_css_properties: list[str]) -> None:
        self.allowed_css_properties = set(allowed_css_properties)

    def sanitize_css(self, style: str) -> str:
        """清洗行内 style，只保留安全属性和值。"""
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
    """判断某个 HTML 属性是否允许保留。"""
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


def _sanitize_style_block(css: str) -> str:
    """清洗 `<style>` 块中的 CSS 规则。"""
    safe_rules: list[str] = []
    for rule in tinycss2.parse_rule_list(css, skip_comments=True, skip_whitespace=True):
        if rule.type != "qualified-rule":
            continue
        selector = tinycss2.serialize(rule.prelude).strip()
        if not selector or any(token in selector.lower() for token in ("@", "expression", "javascript:", "behavior:")):
            continue
        declarations = tinycss2.parse_declaration_list(rule.content, skip_comments=True, skip_whitespace=True)
        safe_declarations: list[str] = []
        for declaration in declarations:
            if declaration.type != "declaration":
                continue
            property_name = declaration.lower_name
            if property_name not in ALLOWED_CSS_PROPERTIES:
                continue
            value = tinycss2.serialize(declaration.value).strip()
            lowered_value = value.lower()
            if any(token in lowered_value for token in ("expression", "javascript:", "vbscript:", "behavior:", "@import")):
                continue
            if "url(" in lowered_value and "data:image/" not in lowered_value:
                continue
            priority = " !important" if declaration.important else ""
            safe_declarations.append(f"{property_name}:{value}{priority}")
        if safe_declarations:
            safe_rules.append(f"{selector}{{{';'.join(safe_declarations)}}}")
    return "\n".join(safe_rules)


def _sanitize_style_blocks(value: str) -> str:
    """遍历并替换 HTML 中所有 `<style>` 块。"""
    def replace_style(match: re.Match[str]) -> str:
        sanitized = _sanitize_style_block(match.group(1))
        return f"<style>{sanitized}</style>" if sanitized else ""

    return re.sub(r"(?is)<style\b[^>]*>(.*?)</style>", replace_style, value)


def _inline_safe_css(value: str) -> str:
    """尽量把安全 CSS 内联进 HTML，便于邮件客户端展示。"""
    sanitized_html = _sanitize_style_blocks(value)
    try:
        return transform(sanitized_html, allow_network=False, remove_classes=False, disable_leftover_css=True)
    except Exception:
        return re.sub(r"(?is)<style\b[^>]*>.*?</style>", "", sanitized_html)


@dataclass(frozen=True)
class MailboxPage:
    """消息列表分页结果。"""

    folder: str
    page: int
    page_size: int
    total: int
    messages: list[dict[str, Any]]
    cached: bool


class MessageOperationRequest(BaseModel):
    """邮件批量操作请求体。"""

    action: str
    uids: list[str] = Field(default_factory=list)
    target_folder: str | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "MessageOperationRequest":
        """校验操作类型、UID 列表和移动目标。"""
        if not self.uids:
            raise ValueError("至少需要选择一封邮件")
        allowed = {"mark_read", "mark_unread", "delete", "move", "flag", "star", "unflag", "unstar"}
        if self.action not in allowed:
            raise ValueError("不支持的邮件操作")
        if self.action == "move" and not self.target_folder:
            raise ValueError("移动邮件必须指定目标文件夹")
        return self


def _imap_settings(session: AuthSession) -> ImapSettings:
    """根据当前登录会话构造 IMAP 参数。"""
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
    """建立并登录 IMAP 会话。"""
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
    """从 IMAP LIST 返回行中提取实际文件夹名称。"""
    match = re.search(r'"([^"]+)"\s*$', line.strip())
    if match:
        return match.group(1)
    return line.strip().split()[-1].strip('"')


def _folder_match_score(candidate: str, aliases: tuple[str, ...]) -> int:
    """为系统文件夹别名匹配打分，分值越高越接近。"""
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
    """在现有文件夹里找出最匹配某个系统文件夹的实际名称。"""
    scored = sorted(
        ((name, _folder_match_score(name, aliases)) for name in existing),
        key=lambda item: item[1],
        reverse=True,
    )
    if scored and scored[0][1] > 0:
        return scored[0][0]
    return canonical


def _status_dict(status_result: Any) -> dict[str, int]:
    """把 IMAP STATUS 响应统一转换为键值字典。"""
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
    """建立系统文件夹规范名到实际文件夹名的映射。"""
    return {
        canonical: _resolve_system_folder(existing, canonical, aliases)
        for canonical, _folder_type, _display_name, aliases in SYSTEM_FOLDERS
    }


def _is_protected_folder(folder: str, folder_map: dict[str, str]) -> bool:
    """判断某个文件夹是否属于系统保留文件夹。"""
    normalized = folder.strip().strip('"')
    return normalized in folder_map or normalized in folder_map.values()


def _folder_exists(folder_name: str, existing: list[str]) -> bool:
    """判断指定文件夹名称是否已经存在。"""
    normalized = folder_name.strip().strip('"')
    if not normalized:
        return False
    lowered = normalized.lower()
    return any(candidate.strip().strip('"').lower() == lowered for candidate in existing)


def _resolved_target_folder(target_folder: str | None, folder_map: dict[str, str]) -> str | None:
    """把前端传入的目标文件夹名转换成 IMAP 实际文件夹名。"""
    if target_folder is None:
        return None
    return folder_map.get(target_folder, target_folder)


def _uid_search(adapter: Any, criteria: str) -> list[str]:
    """兼容不同 adapter 方法名，返回字符串形式的 UID 列表。"""
    if hasattr(adapter, "uid_search"):
        return [str(uid) for uid in adapter.uid_search(criteria)]
    return [str(uid) for uid in adapter.search_uids(criteria)]


def _uid_fetch_message_bytes(adapter: Any, uid: str) -> bytes:
    """兼容不同 adapter 方法名，抓取单封邮件原始字节流。"""
    if hasattr(adapter, "uid_fetch_message_bytes"):
        return adapter.uid_fetch_message_bytes(uid)
    return adapter.fetch_message_bytes(uid)


def _normalize_sender_email(row: dict[str, Any]) -> str:
    """从邮件摘要行中提取并规范化发件人邮箱。"""
    sender = row.get("sender")
    if not isinstance(sender, dict):
        return ""
    return str(sender.get("email") or "").strip().lower()


def _remove_message_records(email: str, folder_name: str, uids: list[str]) -> None:
    """删除本地数据库中已被移动或删除的邮件缓存记录。"""
    normalized_email = email.strip().lower()
    uid_values = [int(str(uid)) for uid in uids if str(uid).isdigit()]
    if not uid_values:
        return
    _remove_preview_records_for_messages(normalized_email, folder_name, uids)
    session_factory = get_session_factory()
    try:
        with session_factory() as db_session:
            account = db_session.scalar(select(MailAccount).where(MailAccount.email == normalized_email))
            if account is None:
                return
            folder = db_session.scalar(select(MailFolder).where(MailFolder.account_id == account.id, MailFolder.name == folder_name))
            if folder is None:
                return
            db_session.execute(
                delete(MailMessage).where(
                    MailMessage.account_id == account.id,
                    MailMessage.folder_id == folder.id,
                    MailMessage.imap_uid.in_(uid_values),
                )
            )
            db_session.commit()
    except Exception:
        return
        folder.total_count = int(
            db_session.scalar(
                select(func.count()).select_from(MailMessage).where(
                    MailMessage.account_id == account.id,
                    MailMessage.folder_id == folder.id,
                )
            )
            or 0
        )
        folder.unread_count = int(
            db_session.scalar(
                select(func.count()).select_from(MailMessage).where(
                    MailMessage.account_id == account.id,
                    MailMessage.folder_id == folder.id,
                    MailMessage.is_read.is_(False),
                )
            )
            or 0
        )
        folder.last_synced_at = datetime.now(timezone.utc)
        db_session.commit()


def _sync_folder_snapshot(email: str, adapter: Any) -> list[dict[str, Any]]:
    """从 IMAP 拉取文件夹快照并同步到本地状态表。"""
    raw_folders = adapter.list_folders()
    existing = [_folder_name_from_list_line(line) for line in raw_folders]
    folders: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for canonical, folder_type, display_name, aliases in SYSTEM_FOLDERS:
        name = _resolve_system_folder(existing, canonical, aliases)
        seen_names.add(name)
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
    for name in sorted(existing):
        if name in seen_names:
            continue
        status_data = _status_dict(adapter.status(name, "(MESSAGES UNSEEN UIDVALIDITY)"))
        folders.append(
            {
                "name": name,
                "canonical_name": name,
                "display_name": name,
                "type": "custom",
                "delimiter": "/",
                "unread_count": int(status_data.get("UNSEEN", 0)),
                "total_count": int(status_data.get("MESSAGES", 0)),
                "uid_validity": status_data.get("UIDVALIDITY"),
            }
        )
    sync_folders(email, folders)
    return folders


def _move_blacklisted_messages(session: AuthSession, adapter: Any, folder: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把命中黑名单且未被白名单豁免的邮件自动搬到垃圾箱。"""
    blacklisted_emails = set(list_blacklisted_contacts(session))
    whitelisted_emails = set(list_whitelisted_contacts(session))
    if not blacklisted_emails:
        return rows
    folder_map = _system_folder_map([_folder_name_from_list_line(line) for line in adapter.list_folders()])
    trash_folder = _resolved_target_folder(".Trash", folder_map) or ".Trash"
    kept_rows: list[dict[str, Any]] = []
    moved_uids: list[str] = []
    for row in rows:
        sender_email = _normalize_sender_email(row)
        if sender_email and sender_email in whitelisted_emails:
            kept_rows.append(row)
            continue
        if sender_email and sender_email in blacklisted_emails:
            adapter.copy_message(str(row["uid"]), str(trash_folder))
            adapter.store_flags(str(row["uid"]), "+FLAGS", "(\\Deleted)")
            moved_uids.append(str(row["uid"]))
            continue
        kept_rows.append(row)
    if moved_uids:
        adapter.expunge()
        _remove_message_records(session.email, folder, moved_uids)
    return kept_rows


def list_folders(session: AuthSession) -> list[dict[str, Any]]:
    """列出当前账号所有文件夹，并附带未读和总数信息。"""
    adapter = _connect_imap(session)
    try:
        ensure_mail_account(session.email)
        return _sync_folder_snapshot(session.email, adapter)
    except MailAdapterError as exc:
        raise AppError(
            "MAILBOX_FOLDER_SYNC_FAILED",
            "同步文件夹失败",
            http_status=status.HTTP_502_BAD_GATEWAY,
            details={"operation": exc.operation},
        ) from exc
    finally:
        adapter.logout()


def create_folder(session: AuthSession, folder_name: str) -> dict[str, Any]:
    """创建一个新的自定义 IMAP 文件夹。"""
    normalized_name = folder_name.strip()
    if not normalized_name:
        raise AppError("FOLDER_NAME_REQUIRED", "文件夹名称不能为空", http_status=status.HTTP_422_UNPROCESSABLE_CONTENT)
    ensure_mail_account(session.email)
    adapter = _connect_imap(session)
    try:
        existing = [_folder_name_from_list_line(line) for line in adapter.list_folders()]
        folder_map = _system_folder_map(existing)
        if _is_protected_folder(normalized_name, folder_map) or _folder_exists(normalized_name, existing):
            raise AppError("FOLDER_ALREADY_EXISTS", "系统文件夹已存在", http_status=status.HTTP_400_BAD_REQUEST)
        adapter.create_folder(normalized_name)
        _sync_folder_snapshot(session.email, adapter)
        return {"folder": normalized_name, "new_name": None, "deleted": False}
    except MailAdapterError as exc:
        raise AppError(
            "MAILBOX_FOLDER_CREATE_FAILED",
            "创建文件夹失败",
            http_status=status.HTTP_502_BAD_GATEWAY,
            details={"operation": exc.operation},
        ) from exc
    finally:
        adapter.logout()


def rename_folder(session: AuthSession, folder_name: str, new_name: str) -> dict[str, Any]:
    """重命名一个已有的自定义 IMAP 文件夹。"""
    old_name = folder_name.strip()
    updated_name = new_name.strip()
    if not old_name or not updated_name:
        raise AppError("FOLDER_NAME_REQUIRED", "文件夹名称不能为空", http_status=status.HTTP_422_UNPROCESSABLE_CONTENT)
    ensure_mail_account(session.email)
    adapter = _connect_imap(session)
    try:
        existing = [_folder_name_from_list_line(line) for line in adapter.list_folders()]
        folder_map = _system_folder_map(existing)
        if _is_protected_folder(old_name, folder_map):
            raise AppError("FOLDER_RENAME_NOT_ALLOWED", "系统文件夹不能重命名", http_status=status.HTTP_400_BAD_REQUEST)
        if _is_protected_folder(updated_name, folder_map) or _folder_exists(updated_name, existing):
            raise AppError("FOLDER_ALREADY_EXISTS", "目标文件夹名称已存在", http_status=status.HTTP_400_BAD_REQUEST)
        adapter.rename_folder(old_name, updated_name)
        _sync_folder_snapshot(session.email, adapter)
        return {"folder": old_name, "new_name": updated_name, "deleted": False}
    except MailAdapterError as exc:
        raise AppError(
            "MAILBOX_FOLDER_RENAME_FAILED",
            "重命名文件夹失败",
            http_status=status.HTTP_502_BAD_GATEWAY,
            details={"operation": exc.operation},
        ) from exc
    finally:
        adapter.logout()


def delete_folder(session: AuthSession, folder_name: str) -> dict[str, Any]:
    """删除一个已有的自定义 IMAP 文件夹。"""
    normalized_name = folder_name.strip()
    if not normalized_name:
        raise AppError("FOLDER_NAME_REQUIRED", "文件夹名称不能为空", http_status=status.HTTP_422_UNPROCESSABLE_CONTENT)
    ensure_mail_account(session.email)
    adapter = _connect_imap(session)
    try:
        existing = [_folder_name_from_list_line(line) for line in adapter.list_folders()]
        folder_map = _system_folder_map(existing)
        if _is_protected_folder(normalized_name, folder_map):
            raise AppError("FOLDER_DELETE_NOT_ALLOWED", "系统文件夹不可删除", http_status=status.HTTP_400_BAD_REQUEST)
        adapter.delete_folder(normalized_name)
        _sync_folder_snapshot(session.email, adapter)
        return {"folder": normalized_name, "new_name": None, "deleted": True}
    except MailAdapterError as exc:
        raise AppError(
            "MAILBOX_FOLDER_DELETE_FAILED",
            "删除文件夹失败",
            http_status=status.HTTP_502_BAD_GATEWAY,
            details={"operation": exc.operation},
        ) from exc
    finally:
        adapter.logout()


def _decode_header_value(value: str | None) -> str:
    """解码 MIME 头字段，尽量返回人类可读文本。"""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _addresses(value: str | None) -> list[dict[str, str]]:
    """把邮件头地址字段解析成统一的姓名和邮箱列表。"""
    result = []
    for name, email_address in getaddresses([value or ""]):
        if not email_address:
            continue
        result.append({"name": _decode_header_value(name), "email": email_address})
    return result


def _message_datetime(message: Message) -> datetime | None:
    """解析邮件 Date 头并返回带时区的时间对象。"""
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
    """清理附件文件名，移除路径和危险控制字符。"""
    name = value.replace("\\", "/").split("/")[-1].strip()
    name = re.sub(r"[\r\n\x00]+", "", name)
    return name or "attachment"


def _attachment_id(index: int) -> str:
    """根据附件顺序生成稳定的附件标识。"""
    return f"att_{index}"


def _attachment_size_bytes(part: Message) -> int:
    """尽量在不解码完整附件内容的前提下估算附件大小。"""
    raw_payload = part.get_payload(decode=False)
    if raw_payload is None:
        return 0
    if isinstance(raw_payload, list):
        return 0
    if isinstance(raw_payload, bytes):
        raw_text = raw_payload.decode("utf-8", errors="ignore")
    else:
        raw_text = str(raw_payload)
    encoding = str(part.get("Content-Transfer-Encoding") or "").strip().lower()
    if encoding == "base64":
        compact = re.sub(r"\s+", "", raw_text)
        padding = compact.count("=")
        return max(0, (len(compact) * 3) // 4 - padding)
    if encoding == "quoted-printable":
        return len(quopri.decodestring(raw_text.encode("utf-8", errors="ignore")))
    return len(raw_text.encode("utf-8"))


def _body_parts(message: Message, *, include_content: bool = False) -> tuple[str, str, list[dict[str, Any]]]:
    """提取邮件 HTML、纯文本正文以及附件列表。"""
    html_body = ""
    text_body = ""
    attachments: list[dict[str, Any]] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        content_disposition = (part.get_content_disposition() or "").lower()
        content_type = part.get_content_type()
        filename = part.get_filename()
        if filename or content_disposition == "attachment":
            payload = (part.get_payload(decode=True) or b"") if include_content else b""
            item = {
                "attachment_id": _attachment_id(len(attachments)),
                "filename": _safe_filename(_decode_header_value(filename or "未命名附件")),
                "content_type": content_type,
                "size_bytes": len(payload) if include_content else _attachment_size_bytes(part),
            }
            if include_content:
                item["content"] = payload
            attachments.append(item)
            continue
        if content_type not in {"text/html", "text/plain"}:
            continue
        payload = part.get_payload(decode=True) or b""
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


def _bodystructure_tokenize(raw: str) -> list[str]:
    """把 IMAP BODYSTRUCTURE 原始字符串切成可递归解析的 token。"""
    tokens: list[str] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char.isspace():
            index += 1
            continue
        if char in "()":
            tokens.append(char)
            index += 1
            continue
        if char == '"':
            index += 1
            buffer: list[str] = []
            while index < len(raw):
                current = raw[index]
                if current == "\\" and index + 1 < len(raw):
                    buffer.append(raw[index + 1])
                    index += 2
                    continue
                if current == '"':
                    index += 1
                    break
                buffer.append(current)
                index += 1
            tokens.append("".join(buffer))
            continue
        start = index
        while index < len(raw) and not raw[index].isspace() and raw[index] not in "()":
            index += 1
        tokens.append(raw[start:index])
    return tokens


def _bodystructure_parse_tokens(tokens: list[str], start: int = 0) -> tuple[Any, int]:
    """把 token 列表递归解析成嵌套 list。"""
    result: list[Any] = []
    index = start
    while index < len(tokens):
        token = tokens[index]
        if token == "(":
            nested, index = _bodystructure_parse_tokens(tokens, index + 1)
            result.append(nested)
            continue
        if token == ")":
            return result, index + 1
        if token.upper() == "NIL":
            result.append(None)
        else:
            result.append(token)
        index += 1
    return result, index


def _bodystructure_params(value: Any) -> dict[str, str]:
    """把 BODYSTRUCTURE 参数列表转换成字典。"""
    if not isinstance(value, list):
        return {}
    result: dict[str, str] = {}
    for index in range(0, len(value) - 1, 2):
        key = str(value[index] or "").lower()
        result[key] = str(value[index + 1] or "")
    return result


def _bodystructure_disposition(value: Any) -> tuple[str, dict[str, str]]:
    """解析 disposition 及其参数。"""
    if not isinstance(value, list) or not value:
        return "", {}
    kind = str(value[0] or "").lower()
    params = _bodystructure_params(value[1] if len(value) > 1 else None)
    return kind, params


def _bodystructure_to_parts(node: Any, section_prefix: str = "") -> list[dict[str, Any]]:
    """把 BODYSTRUCTURE 解析结果展开成带 section 编号的部件列表。"""
    if not isinstance(node, list) or not node:
        return []
    multipart = bool(node and isinstance(node[0], list))
    if multipart:
        parts: list[dict[str, Any]] = []
        child_nodes: list[Any] = []
        for item in node:
            if isinstance(item, list):
                child_nodes.append(item)
            else:
                break
        for index, child in enumerate(child_nodes, start=1):
            section = f"{section_prefix}.{index}" if section_prefix else str(index)
            parts.extend(_bodystructure_to_parts(child, section))
        return parts

    mime_type = str(node[0] or "").lower()
    mime_subtype = str(node[1] or "").lower() if len(node) > 1 else "octet-stream"
    params = _bodystructure_params(node[2] if len(node) > 2 else None)
    encoding = str(node[5] or "").lower() if len(node) > 5 and node[5] is not None else ""
    size_bytes = int(str(node[6])) if len(node) > 6 and str(node[6]).isdigit() else 0
    disposition_kind, disposition_params = _bodystructure_disposition(node[8] if len(node) > 8 else None)
    filename = (
        disposition_params.get("filename")
        or params.get("name")
        or disposition_params.get("name")
        or "attachment"
    )
    return [
        {
            "section": section_prefix or "1",
            "content_type": f"{mime_type}/{mime_subtype}",
            "params": params,
            "encoding": encoding,
            "size_bytes": size_bytes,
            "disposition": disposition_kind,
            "filename": _safe_filename(_decode_header_value(filename)),
        }
    ]


def _parse_bodystructure_parts(raw: bytes) -> list[dict[str, Any]]:
    """从 IMAP BODYSTRUCTURE 原文中提取各部件 section 与元数据。"""
    text = raw.decode("utf-8", errors="replace")
    marker = "BODYSTRUCTURE"
    marker_index = text.upper().find(marker)
    if marker_index == -1:
        return []
    body = text[marker_index + len(marker):].strip()
    if not body.startswith("("):
        return []
    tokens = _bodystructure_tokenize(body)
    parsed, _ = _bodystructure_parse_tokens(tokens)
    if not parsed:
        return []
    root = parsed[0] if len(parsed) == 1 and isinstance(parsed[0], list) else parsed
    return _bodystructure_to_parts(root)


def _decode_section_bytes(content: bytes, content_type: str, charset: str | None = None) -> str:
    """把 section 抓取到的正文 bytes 解码成字符串。"""
    resolved_charset = charset or "utf-8"
    try:
        return content.decode(resolved_charset, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


def _looks_like_base64_section(content: bytes) -> bool:
    """尽量判断当前 section 是否仍然是 base64 传输编码文本。"""
    compact = re.sub(rb"\s+", b"", content)
    if not compact or len(compact) % 4 != 0:
        return False
    if re.fullmatch(rb"[A-Za-z0-9+/=]+", compact) is None:
        return False
    return True


def _decode_section_transfer_bytes(content: bytes, transfer_encoding: str | None = None) -> bytes:
    """按 Content-Transfer-Encoding 把 IMAP section 原文还原成真实字节。"""
    encoding = str(transfer_encoding or "").strip().lower()
    if encoding == "base64":
        if not _looks_like_base64_section(content):
            return content
        compact = re.sub(rb"\s+", b"", content)
        try:
            return b64decode(compact, validate=False)
        except Exception:
            return content
    if encoding == "quoted-printable":
        if b"=" not in content:
            return content
        try:
            return quopri.decodestring(content)
        except Exception:
            return content
    return content


def _message_detail_from_sections(adapter: Any, uid: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """优先通过 HEADER + BODYSTRUCTURE + BODY section 生成摘要和详情。"""
    if not all(hasattr(adapter, method) for method in ("uid_fetch_headers", "uid_fetch_bodystructure", "uid_fetch_body_section")):
        return None
    headers_raw = adapter.uid_fetch_headers(uid)
    structure_raw = adapter.uid_fetch_bodystructure(uid)
    parts = _parse_bodystructure_parts(structure_raw)
    if not headers_raw or not parts:
        return None

    header_message = BytesHeaderParser(policy=default).parsebytes(headers_raw)
    text_part = next(
        (
            item for item in parts
            if item.get("content_type") == "text/plain" and item.get("disposition") != "attachment"
        ),
        None,
    )
    html_part = next(
        (
            item for item in parts
            if item.get("content_type") == "text/html" and item.get("disposition") != "attachment"
        ),
        None,
    )
    text_body = ""
    html_body = ""
    if text_part:
        text_body = _decode_section_bytes(
            _decode_section_transfer_bytes(
                adapter.uid_fetch_body_section(uid, str(text_part["section"])),
                str(text_part.get("encoding") or ""),
            ),
            str(text_part.get("content_type") or "text/plain"),
            str(text_part.get("params", {}).get("charset") or "utf-8"),
        )
    if html_part:
        html_body = _decode_section_bytes(
            _decode_section_transfer_bytes(
                adapter.uid_fetch_body_section(uid, str(html_part["section"])),
                str(html_part.get("encoding") or ""),
            ),
            str(html_part.get("content_type") or "text/html"),
            str(html_part.get("params", {}).get("charset") or "utf-8"),
        )

    attachment_candidates = [
        item
        for item in parts
        if item.get("disposition") == "attachment"
        or (item.get("filename") not in {"", "attachment"} and not str(item.get("content_type", "")).startswith("text/"))
    ]
    attachments = [
        {
            "attachment_id": _attachment_id(index),
            "filename": str(item.get("filename") or "attachment"),
            "content_type": str(item.get("content_type") or "application/octet-stream"),
            "size_bytes": int(item.get("size_bytes") or 0),
            "section": str(item.get("section") or ""),
            "encoding": str(item.get("encoding") or ""),
        }
        for index, item in enumerate(attachment_candidates)
    ]

    sent_at = _message_datetime(header_message)
    sender = _addresses(header_message.get("From"))
    recipients = _addresses(header_message.get("To"))
    cc_recipients = _addresses(header_message.get("Cc"))
    subject = _decode_header_value(header_message.get("Subject")) or "(无主题)"
    safe_html = _clean_html(html_body) if html_body else _text_to_html(text_body)
    summary = {
        "uid": str(uid),
        "message_id": header_message.get("Message-ID"),
        "subject": subject,
        "sender": sender[0] if sender else {"name": "", "email": ""},
        "to": recipients,
        "cc": cc_recipients,
        "date": sent_at.isoformat() if sent_at else None,
        "read": _read_flag(header_message),
        "has_attachments": bool(attachments),
        "attachment_types": sorted({_attachment_category(str(item["content_type"])) for item in attachments if item.get("content_type")}),
        "snippet": _snippet(html_body, text_body),
        "html_body": html_body,
        "text_body": text_body,
    }
    detail = {
        "uid": str(uid),
        "message_id": header_message.get("Message-ID"),
        "subject": subject,
        "from": sender,
        "to": recipients,
        "cc": cc_recipients,
        "date": sent_at.isoformat() if sent_at else None,
        "html_body": safe_html,
        "text_body": text_body,
        "read": _read_flag(header_message),
        "attachments": attachments,
    }
    return summary, detail


def _clean_html(value: str) -> str:
    """对 HTML 邮件正文做安全清洗，同时尽量保留样式表现。"""
    value = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", value)
    value = _inline_safe_css(value)
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
    return cleaned


def _link_attrs(attrs: dict[tuple[str | None, str], str], new: bool = False) -> dict[tuple[str | None, str], str]:
    """为正文中的链接补安全属性，并剔除危险 href。"""
    href_key = (None, "href")
    href = attrs.get(href_key, "")
    if href.lower().startswith("javascript:"):
        attrs.pop(href_key, None)
    attrs[(None, "rel")] = "noopener noreferrer"
    attrs[(None, "target")] = "_blank"
    return attrs


def _text_to_html(value: str) -> str:
    """把纯文本正文转成简单 HTML 以便前端展示。"""
    return "<br>".join(html.escape(value).splitlines())


def _snippet(html_body: str, text_body: str) -> str:
    """生成邮件列表展示用的短摘要。"""
    source = text_body or re.sub(r"<[^>]+>", " ", html_body)
    return re.sub(r"\s+", " ", source).strip()[:180]


def _attachment_category(content_type: str) -> str:
    """按 MIME 类型把附件归类为图片、文档、压缩包等类别。"""
    lowered = content_type.strip().lower()
    if lowered.startswith("image/"):
        return "image"
    if lowered.startswith("audio/"):
        return "audio"
    if lowered.startswith("video/"):
        return "video"
    if lowered == "application/pdf":
        return "pdf"
    if lowered in {
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/rtf",
        "application/vnd.oasis.opendocument.text",
        "text/plain",
        "text/rtf",
    }:
        return "document"
    if lowered in {
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv",
    }:
        return "spreadsheet"
    if lowered in {
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }:
        return "presentation"
    if lowered in {
        "application/zip",
        "application/x-zip-compressed",
        "application/x-tar",
        "application/gzip",
        "application/x-gzip",
        "application/x-7z-compressed",
        "application/x-rar-compressed",
    }:
        return "archive"
    return "other"


def _read_flag(message: Message) -> bool:
    """从邮件头信息推断这封邮件是否已读。"""
    flags = " ".join(message.get_all("X-IMAP-Flags", []))
    status_value = message.get("Status", "")
    return "\\Seen" in flags or "R" in status_value


def _message_summary(uid: str, raw: bytes) -> dict[str, Any]:
    """把原始邮件字节解析成列表页所需的摘要结构。"""
    message = message_from_bytes(raw, policy=default)
    html_body, text_body, attachments = _body_parts(message)
    sent_at = _message_datetime(message)
    sender = _addresses(message.get("From"))
    recipients = _addresses(message.get("To"))
    attachment_types = sorted({_attachment_category(item["content_type"]) for item in attachments if item.get("content_type")})
    return {
        "uid": str(uid),
        "message_id": message.get("Message-ID"),
        "subject": _decode_header_value(message.get("Subject")) or "(无主题)",
        "sender": sender[0] if sender else {"name": "", "email": ""},
        "to": recipients,
        "cc": _addresses(message.get("Cc")),
        "date": sent_at.isoformat() if sent_at else None,
        "read": _read_flag(message),
        "has_attachments": bool(attachments),
        "attachment_types": attachment_types,
        "snippet": _snippet(html_body, text_body),
        "html_body": html_body,
        "text_body": text_body,
    }


def _message_list_item(row: dict[str, Any]) -> dict[str, Any]:
    """裁剪摘要数据，只保留列表接口需要暴露的字段。"""
    return {
        "uid": str(row.get("uid") or ""),
        "message_id": row.get("message_id"),
        "subject": str(row.get("subject") or "(无主题)"),
        "sender": row.get("sender") if isinstance(row.get("sender"), dict) else {"name": "", "email": ""},
        "to": row.get("to") if isinstance(row.get("to"), list) else [],
        "cc": row.get("cc") if isinstance(row.get("cc"), list) else [],
        "date": row.get("date"),
        "read": bool(row.get("read")),
        "has_attachments": bool(row.get("has_attachments")),
        "attachment_types": row.get("attachment_types") if isinstance(row.get("attachment_types"), list) else [],
        "snippet": str(row.get("snippet") or ""),
    }


def _message_detail(uid: str, raw: bytes) -> dict[str, Any]:
    """把原始邮件字节解析成详情页需要的完整结构。"""
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
        "read": _read_flag(message),
        "attachments": attachments,
    }


def _message_cache_key(email: str, folder: str, page: int, page_size: int) -> str:
    """生成列表分页缓存键。"""
    return f"mail:list:{email}:{folder}:{page}:{page_size}"


def _search_cache_key(
    email: str,
    folder: str,
    query: str,
    page: int,
    page_size: int,
    sender: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    has_attachments: bool | None = None,
) -> str:
    """生成带搜索条件的缓存键。"""
    return ":".join(
        [
            "mail",
            "search",
            email,
            folder,
            query,
            sender,
            date_from.isoformat() if date_from else "",
            date_to.isoformat() if date_to else "",
            "" if has_attachments is None else ("1" if has_attachments else "0"),
            str(page),
            str(page_size),
        ]
    )


def _message_search_text(row: dict[str, Any]) -> str:
    """汇总主题、发件人、正文和收件人，生成全文检索文本。"""
    sender = row.get("sender") if isinstance(row.get("sender"), dict) else {}
    recipients = row.get("to") if isinstance(row.get("to"), list) else []
    cc_recipients = row.get("cc") if isinstance(row.get("cc"), list) else []
    parts: list[str] = [
        str(row.get("subject") or ""),
        str(sender.get("name") or ""),
        str(sender.get("email") or ""),
        str(row.get("snippet") or ""),
        str(row.get("text_body") or ""),
        re.sub(r"<[^>]+>", " ", str(row.get("html_body") or "")),
    ]
    for recipient in [*recipients, *cc_recipients]:
        if not isinstance(recipient, dict):
            parts.append(str(recipient or ""))
            continue
        parts.append(str(recipient.get("name") or ""))
        parts.append(str(recipient.get("email") or ""))
    return re.sub(r"\s+", " ", " ".join(part for part in parts if part)).strip().lower()


def _message_detail_cache_key(email: str, folder: str, uid: str) -> str:
    """生成单封邮件详情缓存键。"""
    return f"mail:detail:{email.strip().lower()}:{folder}:{uid}"


def _message_row_cache_key(email: str, folder: str, uid: str) -> str:
    """生成单封邮件摘要缓存键，供无数据库环境下回填搜索/列表。"""
    return f"mail:row:{email.strip().lower()}:{folder}:{uid}"


def _message_attachment_cache_key(email: str, folder: str, uid: str, attachment_id: str) -> str:
    """生成单个附件内容缓存键。"""
    return f"mail:attachment:{email.strip().lower()}:{folder}:{uid}:{attachment_id}"


def _cached_message_detail(session: AuthSession, folder: str, uid: str) -> dict[str, Any] | None:
    """读取缓存中的完整邮件详情。"""
    detail_cache = JsonCache(redis_client.get_redis_client())
    cached = detail_cache.get(_message_detail_cache_key(session.email, folder, uid))
    if not isinstance(cached, dict):
        return None
    attachments = cached.get("attachments")
    if not isinstance(attachments, list):
        attachments = []
    return {
        "uid": str(cached.get("uid") or uid),
        "message_id": cached.get("message_id"),
        "subject": str(cached.get("subject") or "(无主题)"),
        "from": cached.get("from") if isinstance(cached.get("from"), list) else [],
        "to": cached.get("to") if isinstance(cached.get("to"), list) else [],
        "cc": cached.get("cc") if isinstance(cached.get("cc"), list) else [],
        "date": cached.get("date"),
        "html_body": str(cached.get("html_body") or ""),
        "text_body": str(cached.get("text_body") or ""),
        "read": bool(cached.get("read")),
        "attachments": _annotate_attachment_preview_state(
            session,
            folder,
            uid,
            [item for item in attachments if isinstance(item, dict)],
        ),
    }


def _build_message_detail_from_raw(uid: str, raw: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    """从原始邮件一次性生成摘要和详情，避免重复解析。"""
    return _message_summary(uid, raw), _message_detail(uid, raw)


def _load_cached_attachment(session: AuthSession, folder: str, uid: str, attachment_id: str) -> dict[str, Any] | None:
    """优先读取已缓存的单附件内容。"""
    attachment_cache = JsonCache(redis_client.get_redis_client())
    cached = attachment_cache.get(_message_attachment_cache_key(session.email, folder, uid, attachment_id))
    if not isinstance(cached, dict):
        return None
    content_b64 = cached.get("content_b64")
    if not isinstance(content_b64, str):
        return None
    try:
        content = b64decode(content_b64.encode("ascii"))
    except Exception:
        return None
    return {
        "attachment_id": attachment_id,
        "filename": str(cached.get("filename") or "attachment"),
        "content_type": str(cached.get("content_type") or "application/octet-stream"),
        "size_bytes": int(cached.get("size_bytes") or len(content)),
        "section": str(cached.get("section") or ""),
        "content": content,
    }


def _load_cached_message_raw(session: AuthSession, folder: str, uid: str) -> bytes | None:
    """读取详情缓存里附带的原始 RFC822 内容。"""
    detail_cache = JsonCache(redis_client.get_redis_client())
    cached = detail_cache.get(_message_detail_cache_key(session.email, folder, uid))
    if not isinstance(cached, dict):
        return None
    raw_b64 = cached.get("raw_b64")
    if not isinstance(raw_b64, str) or not raw_b64:
        return None
    try:
        return b64decode(raw_b64.encode("ascii"))
    except Exception:
        return None


def _cache_attachment_contents(session: AuthSession, folder: str, uid: str, attachments: list[dict[str, Any]]) -> None:
    """把解析后的附件二进制内容按单附件缓存，避免重复全量 MIME 解析。"""
    attachment_cache = JsonCache(redis_client.get_redis_client())
    for item in attachments:
        attachment_id = str(item.get("attachment_id") or "")
        content = item.get("content")
        if not attachment_id or not isinstance(content, (bytes, bytearray)):
            continue
        attachment_cache.set(
            _message_attachment_cache_key(session.email, folder, uid, attachment_id),
            {
                "filename": str(item.get("filename") or "attachment"),
                "content_type": str(item.get("content_type") or "application/octet-stream"),
                "size_bytes": int(item.get("size_bytes") or len(content)),
                "section": str(item.get("section") or ""),
                "content_b64": b64encode(bytes(content)).decode("ascii"),
            },
            ttl_seconds=3600,
        )


def _schedule_attachment_preview_generation(session: AuthSession, folder: str, uid: str, attachment: dict[str, Any]) -> None:
    """为单个可预览附件异步生成预览产物，并做进程内去重。"""
    attachment_id = str(attachment.get("attachment_id") or "")
    filename = str(attachment.get("filename") or "attachment")
    content_type = str(attachment.get("content_type") or "application/octet-stream")
    if not attachment_id or _preview_kind(filename, content_type) is None:
        return

    record = _ensure_preview_record(session, folder, uid, attachment, status_hint="pending")
    if record is None or record.status == "ready":
        return

    task_key = _preview_task_key(session.email, folder, uid, attachment_id)
    with PREVIEW_TASK_LOCK:
        if task_key in PREVIEW_RUNNING_TASKS:
            return
        PREVIEW_RUNNING_TASKS.add(task_key)

    def run() -> None:
        try:
            _build_preview_record_payload(session, folder, uid, attachment_id)
        except AppError as exc:
            _mark_preview_record_failed(session.email, folder, uid, attachment_id, exc.message)
        except Exception as exc:  # pragma: no cover - 后台线程异常以状态回写为主
            _mark_preview_record_failed(session.email, folder, uid, attachment_id, f"预览生成失败: {exc.__class__.__name__}")
        finally:
            with PREVIEW_TASK_LOCK:
                PREVIEW_RUNNING_TASKS.discard(task_key)

    MAILBOX_BACKGROUND_EXECUTOR.submit(run)


def _schedule_attachment_preview_generation_from_detail(
    session: AuthSession,
    folder: str,
    uid: str,
    detail: dict[str, Any],
) -> None:
    """批量调度当前邮件中可预览附件的预热任务。"""
    attachments = detail.get("attachments") if isinstance(detail.get("attachments"), list) else []
    for item in attachments:
        if isinstance(item, dict):
            _schedule_attachment_preview_generation(session, folder, uid, item)
    _schedule_preview_housekeeping()


def _annotate_attachment_preview_state(session: AuthSession, folder: str, uid: str, attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把预览状态附着到附件元数据上，供前端静默加载和轮询。"""
    normalized_email = session.email.strip().lower()
    attachment_ids = [str(item.get("attachment_id") or "") for item in attachments if isinstance(item, dict)]
    status_map: dict[str, dict[str, Any]] = {}
    if attachment_ids:
        session_factory = get_session_factory()
        with session_factory() as db_session:
            account, _message_row = _find_message_cache_row(db_session, normalized_email, folder, uid)
            if account is not None:
                records = db_session.scalars(
                    select(MailAttachmentPreview).where(
                        MailAttachmentPreview.account_id == account.id,
                        MailAttachmentPreview.folder_name == folder,
                        MailAttachmentPreview.imap_uid == int(str(uid)),
                        MailAttachmentPreview.attachment_id.in_(attachment_ids),
                    )
                ).all()
                status_map = {record.attachment_id: _preview_status_payload(record) for record in records}

    annotated: list[dict[str, Any]] = []
    for item in attachments:
        if not isinstance(item, dict):
            continue
        copied = dict(item)
        attachment_id = str(copied.get("attachment_id") or "")
        filename = str(copied.get("filename") or "attachment")
        content_type = str(copied.get("content_type") or "application/octet-stream")
        copied["preview_kind"] = _preview_kind(filename, content_type)
        copied["preview_status"] = status_map.get(attachment_id, {"status": "missing", "ready": False})
        copied["preview_ready"] = bool(copied["preview_status"].get("ready"))
        annotated.append(copied)
    return annotated


def _prewarm_message_details(session: AuthSession, folder: str, uids: list[str]) -> None:
    """后台预热一批邮件详情缓存，不阻塞列表返回。"""
    target_uids = [str(uid) for uid in uids if str(uid)]
    if not target_uids:
        return
    adapter = _connect_imap(session)
    try:
        adapter.select_folder(folder)
        for uid in target_uids:
            if _cached_message_detail(session, folder, uid) is not None:
                continue
            raw = _uid_fetch_message_bytes(adapter, uid)
            summary, detail = _build_message_detail_from_raw(uid, raw)
            _cache_attachment_sections_from_detail(session, adapter, folder, uid, detail)
            _persist_message_cache(session, folder, summary, detail, raw_message=raw)
            _schedule_attachment_preview_generation_from_detail(session, folder, uid, detail)
    except Exception:
        return
    finally:
        try:
            adapter.logout()
        except Exception:
            pass


def _prewarm_attachment_sections_from_cached_detail(session: AuthSession, folder: str, uids: list[str]) -> None:
    """基于已缓存详情优先预热前几封附件邮件的正文 section 和附件内容。"""
    target_uids = [str(uid) for uid in uids if str(uid)]
    if not target_uids:
        return
    adapter = _connect_imap(session)
    try:
        adapter.select_folder(folder)
        for uid in target_uids:
            cached_detail = _cached_message_detail(session, folder, uid)
            if cached_detail is None:
                continue
            _cache_attachment_sections_from_detail(session, adapter, folder, uid, cached_detail)
    except Exception:
        return
    finally:
        try:
            adapter.logout()
        except Exception:
            pass


def _schedule_detail_prewarm(session: AuthSession, folder: str, uids: list[str], *, limit: int = DETAIL_PREWARM_LIMIT) -> None:
    """异步预热当前页最可能被打开的若干封邮件详情。"""
    if _is_test_runtime():
        return
    candidate_uids: list[str] = []
    attachment_candidate_uids: list[str] = []
    for uid in uids:
        uid_text = str(uid)
        if not uid_text:
            continue
        cached_detail = _cached_message_detail(session, folder, uid_text)
        if cached_detail is None:
            candidate_uids.append(uid_text)
            continue
        attachments = cached_detail.get("attachments") if isinstance(cached_detail.get("attachments"), list) else []
        if any(
            isinstance(item, dict) and _preview_kind(str(item.get("filename") or "attachment"), str(item.get("content_type") or ""))
            for item in attachments
        ):
            attachment_candidate_uids.append(uid_text)
    if not candidate_uids and not attachment_candidate_uids:
        return
    prioritized_uids: list[str] = []
    for uid in attachment_candidate_uids:
        if uid not in prioritized_uids:
            prioritized_uids.append(uid)
        if len(prioritized_uids) >= ATTACHMENT_PREWARM_LIMIT:
            break
    for uid in candidate_uids:
        if uid not in prioritized_uids:
            prioritized_uids.append(uid)
        if len(prioritized_uids) >= limit:
            break
    if prioritized_uids:
        MAILBOX_BACKGROUND_EXECUTOR.submit(_prewarm_message_details, session, folder, prioritized_uids)
    if attachment_candidate_uids:
        MAILBOX_BACKGROUND_EXECUTOR.submit(
            _prewarm_attachment_sections_from_cached_detail,
            session,
            folder,
            attachment_candidate_uids[:ATTACHMENT_PREWARM_LIMIT],
        )
    if attachment_candidate_uids:
        MAILBOX_BACKGROUND_EXECUTOR.submit(_prewarm_cached_attachment_previews, session, folder, attachment_candidate_uids)


def _prewarm_cached_attachment_previews(session: AuthSession, folder: str, uids: list[str]) -> None:
    """基于已缓存详情直接前置触发附件预览生成，避免首点才排队。"""
    for uid in uids:
        uid_text = str(uid)
        if not uid_text:
            break
        cached_detail = _cached_message_detail(session, folder, uid_text)
        if cached_detail is None:
            continue
        _schedule_attachment_preview_generation_from_detail(session, folder, uid_text, cached_detail)


def _mark_message_read_async(session: AuthSession, folder: str, uid: str) -> None:
    """后台执行打开即已读，避免阻塞正文详情返回。"""
    try:
        adapter = _connect_imap(session)
        try:
            adapter.select_folder(folder)
            adapter.mark_seen(uid)
            persist_message_read_state(session.email, folder, [uid], is_read=True)
            _sync_folder_snapshot(session.email, adapter)
            _invalidate_message_cache(session.email, [folder])
        finally:
            adapter.logout()
    except Exception:
        return


def _schedule_mark_message_read(session: AuthSession, folder: str, uid: str) -> None:
    """异步调度打开即已读任务。"""
    MAILBOX_BACKGROUND_EXECUTOR.submit(_mark_message_read_async, session, folder, uid)


def _cache_attachment_sections_from_detail(session: AuthSession, adapter: Any, folder: str, uid: str, detail: dict[str, Any]) -> None:
    """基于详情中的 section 元数据预热附件内容缓存。"""
    attachments = detail.get("attachments") if isinstance(detail.get("attachments"), list) else []
    cached_items: list[dict[str, Any]] = []
    for item in attachments:
        if not isinstance(item, dict):
            continue
        section = str(item.get("section") or "")
        if not section:
            continue
        try:
            content = _decode_section_transfer_bytes(
                adapter.uid_fetch_body_section(uid, section),
                str(item.get("encoding") or ""),
            )
        except Exception:
            continue
        cached_items.append(
            {
                "attachment_id": str(item.get("attachment_id") or ""),
                "filename": str(item.get("filename") or "attachment"),
                "content_type": str(item.get("content_type") or "application/octet-stream"),
                "size_bytes": int(item.get("size_bytes") or len(content)),
                "section": section,
                "encoding": str(item.get("encoding") or ""),
                "content": content,
            }
        )
    if cached_items:
        _cache_attachment_contents(session, folder, uid, cached_items)


def _preview_kind(filename: str, content_type: str) -> str | None:
    """根据附件类型判断是否支持预览，以及应走哪种预览形态。"""
    lower_name = filename.lower()
    lower_type = content_type.lower()
    if lower_type.startswith("image/") or re.search(r"\.(png|jpe?g|gif|webp|bmp|svg)$", lower_name):
        return "image"
    if lower_type == "application/pdf" or lower_name.endswith(".pdf"):
        return "pdf"
    if (
        lower_type.startswith("text/")
        or lower_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or re.search(r"\.(txt|md|json|docx)$", lower_name)
    ):
        return "text"
    return None


def _attachment_supports_thumbnail(filename: str, content_type: str) -> bool:
    """判断附件卡片是否应该生成首屏封面缩略图。"""
    lower_name = filename.lower()
    lower_type = content_type.lower()
    return (
        lower_type == "application/pdf"
        or lower_name.endswith(".pdf")
        or lower_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or lower_name.endswith(".docx")
    )


def _preview_storage_key(
    email: str,
    folder: str,
    uid: str,
    attachment_id: str,
    filename: str,
    content_type: str,
) -> str:
    """为附件预览生成稳定的存储键。"""
    kind = _preview_kind(filename, content_type) or "file"
    extension = ".html" if (
        content_type.lower() == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or filename.lower().endswith(".docx")
    ) else ".bin"
    digest = hashlib.sha256(
        "::".join(
            [
                email.strip().lower(),
                folder,
                uid,
                attachment_id,
                filename,
                content_type,
                kind,
            ]
        ).encode("utf-8")
    ).hexdigest()
    return f"{digest}{extension}"


def _preview_thumbnail_storage_key(
    email: str,
    folder: str,
    uid: str,
    attachment_id: str,
    filename: str,
    content_type: str,
    thumbnail_content_type: str,
) -> str:
    """为附件缩略图生成稳定的存储键。"""
    extension = ".svg" if thumbnail_content_type == "image/svg+xml" else ".png"
    digest = hashlib.sha256(
        "::".join(
            [
                email.strip().lower(),
                folder,
                uid,
                attachment_id,
                filename,
                content_type,
                "thumbnail",
            ]
        ).encode("utf-8")
    ).hexdigest()
    return f"{digest}{extension}"


def _preview_cache_root() -> Path:
    """返回附件预览产物缓存目录。"""
    return Path(get_settings().attachment_preview_cache_dir).expanduser()


def _preview_cache_path_from_storage_key(storage_key: str) -> tuple[Path, Path]:
    """根据稳定存储键解析产物文件和元数据文件路径。"""
    root = _preview_cache_root()
    digest = storage_key.split(".", 1)[0]
    directory = root / digest[:2] / digest[2:4]
    return directory / storage_key, directory / f"{digest}.json"


def _preview_cache_path(
    session: AuthSession,
    folder: str,
    uid: str,
    attachment_id: str,
    filename: str,
    content_type: str,
) -> tuple[Path, Path]:
    """兼容旧调用方式，根据附件上下文生成缓存路径。"""
    storage_key = _preview_storage_key(session.email, folder, uid, attachment_id, filename, content_type)
    return _preview_cache_path_from_storage_key(storage_key)


def _preview_source_hash(folder: str, uid: str, attachment_id: str, filename: str, content_type: str, size_bytes: int) -> str:
    """生成附件源内容的稳定签名，用于识别是否需要重建预览。"""
    return hashlib.sha256(
        "::".join(
            [
                folder,
                uid,
                attachment_id,
                filename,
                content_type,
                str(size_bytes),
            ]
        ).encode("utf-8")
    ).hexdigest()


def _normalize_preview_time(value: datetime | None) -> datetime:
    """把数据库里可能混杂的 naive/aware 时间统一规范为 UTC aware。"""
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _cleanup_preview_cache_if_needed() -> None:
    """按 TTL、容量和孤儿记录清理附件预览缓存目录与索引。"""
    settings = get_settings()
    root = _preview_cache_root()
    if not root.exists():
        return

    ttl_seconds = max(int(settings.attachment_preview_cache_ttl_seconds), 0)
    max_bytes = max(int(settings.attachment_preview_cache_max_mb), 1) * 1024 * 1024
    processing_timeout = max(int(settings.attachment_preview_processing_timeout_seconds), 1)
    now = datetime.now(timezone.utc)
    session_factory = get_session_factory()

    with session_factory() as db_session:
        records = db_session.scalars(select(MailAttachmentPreview)).all()
        referenced_stems: set[str] = set()
        ready_candidates: list[tuple[datetime, int, MailAttachmentPreview, Path]] = []
        total_bytes = 0

        for record in records:
            digest = record.storage_key.split(".", 1)[0]
            path, meta_path = _preview_cache_path_from_storage_key(record.storage_key)
            referenced_stems.add(digest)
            preview_meta = _load_cached_preview(path, meta_path) if path.exists() and meta_path.exists() else None
            thumbnail_storage_key = (
                preview_meta.get("thumbnail_storage_key")
                if isinstance(preview_meta, dict) and isinstance(preview_meta.get("thumbnail_storage_key"), str)
                else None
            )
            thumb_path: Path | None = None
            thumb_meta_path: Path | None = None
            if thumbnail_storage_key:
                thumb_path, thumb_meta_path = _preview_cache_path_from_storage_key(thumbnail_storage_key)
                referenced_stems.add(thumbnail_storage_key.split(".", 1)[0])
            if record.status == "processing" and (now - record.updated_at).total_seconds() > processing_timeout:
                record.status = "failed"
                record.error_message = "后台生成超时，下次访问会重新触发"

            reference_time = _normalize_preview_time(record.last_accessed_at or record.generated_at or record.updated_at or record.created_at)
            is_expired = ttl_seconds > 0 and (now - reference_time).total_seconds() > ttl_seconds
            if is_expired or (record.status == "failed" and ttl_seconds > 0 and (now - record.updated_at).total_seconds() > ttl_seconds):
                try:
                    path.unlink(missing_ok=True)
                    meta_path.unlink(missing_ok=True)
                    if thumb_path is not None and thumb_meta_path is not None:
                        thumb_path.unlink(missing_ok=True)
                        thumb_meta_path.unlink(missing_ok=True)
                except OSError:
                    pass
                db_session.delete(record)
                continue

            if record.status == "ready" and path.exists():
                if _attachment_supports_thumbnail(record.filename, record.source_content_type) and not _preview_thumbnail_is_ready(record):
                    record.status = "pending"
                    record.error_message = "附件缩略图缺失，将在下次访问时重建"
                    continue
                try:
                    size = int(path.stat().st_size)
                except OSError:
                    size = int(record.preview_size_bytes or 0)
                if thumb_path is not None and thumb_path.exists():
                    try:
                        size += int(thumb_path.stat().st_size)
                    except OSError:
                        pass
                total_bytes += size
                ready_candidates.append((_normalize_preview_time(record.last_accessed_at or record.generated_at or record.updated_at), size, record, path))
            elif record.status == "ready" and not path.exists():
                record.status = "pending"
                record.error_message = "预览文件缺失，将在下次访问时重建"
        if total_bytes > max_bytes:
            ready_candidates.sort(key=lambda item: item[0])
            for _accessed_at, size, record, path in ready_candidates:
                if total_bytes <= max_bytes:
                    break
                meta_path = _preview_cache_path_from_storage_key(record.storage_key)[1]
                preview_meta = _load_cached_preview(path, meta_path) if path.exists() and meta_path.exists() else None
                thumbnail_storage_key = (
                    preview_meta.get("thumbnail_storage_key")
                    if isinstance(preview_meta, dict) and isinstance(preview_meta.get("thumbnail_storage_key"), str)
                    else None
                )
                try:
                    path.unlink(missing_ok=True)
                    meta_path.unlink(missing_ok=True)
                    if thumbnail_storage_key:
                        thumb_path, thumb_meta_path = _preview_cache_path_from_storage_key(thumbnail_storage_key)
                        thumb_path.unlink(missing_ok=True)
                        thumb_meta_path.unlink(missing_ok=True)
                except OSError:
                    pass
                total_bytes -= size
                db_session.delete(record)

        db_session.commit()

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix == ".tmp":
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        stem = path.stem
        if stem not in referenced_stems:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _load_cached_preview(path: Path, meta_path: Path) -> dict[str, Any] | None:
    """读取已生成的附件预览产物。"""
    if not path.exists() or not meta_path.exists():
        return None
    try:
        payload = path.read_bytes()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return {
        "content": payload,
        "content_type": str(meta.get("content_type") or "application/octet-stream"),
        "filename": str(meta.get("filename") or path.name),
        "thumbnail_storage_key": meta.get("thumbnail_storage_key"),
        "thumbnail_content_type": meta.get("thumbnail_content_type"),
        "thumbnail_cache_version": meta.get("thumbnail_cache_version"),
    }


def _persist_preview_cache(
    path: Path,
    meta_path: Path,
    *,
    filename: str,
    content_type: str,
    content: bytes,
    thumbnail_storage_key: str | None = None,
    thumbnail_content_type: str | None = None,
    thumbnail_cache_version: str | None = None,
) -> None:
    """把预览产物及其元数据落盘。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_meta_path = meta_path.with_suffix(".tmp")
    temp_path.write_bytes(content)
    temp_meta_path.write_text(
        json.dumps(
            {
                "filename": filename,
                "content_type": content_type,
                "thumbnail_storage_key": thumbnail_storage_key,
                "thumbnail_content_type": thumbnail_content_type,
                "thumbnail_cache_version": thumbnail_cache_version,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temp_path.replace(path)
    temp_meta_path.replace(meta_path)


def _find_message_cache_row(db_session: Any, email: str, folder: str, uid: str) -> tuple[MailAccount | None, MailMessage | None]:
    """按邮箱、文件夹和 UID 查找本地缓存的账户和消息行。"""
    normalized_email = email.strip().lower()
    account = db_session.scalar(select(MailAccount).where(MailAccount.email == normalized_email))
    if account is None:
        return None, None
    folder_row = db_session.scalar(select(MailFolder).where(MailFolder.account_id == account.id, MailFolder.name == folder))
    if folder_row is None:
        return account, None
    message_row = db_session.scalar(
        select(MailMessage).where(
            MailMessage.account_id == account.id,
            MailMessage.folder_id == folder_row.id,
            MailMessage.imap_uid == int(str(uid)),
        )
    )
    return account, message_row


def _lookup_preview_record(db_session: Any, email: str, folder: str, uid: str, attachment_id: str) -> MailAttachmentPreview | None:
    """读取单个附件对应的预览索引记录。"""
    account, _message_row = _find_message_cache_row(db_session, email, folder, uid)
    if account is None:
        return None
    return db_session.scalar(
        select(MailAttachmentPreview).where(
            MailAttachmentPreview.account_id == account.id,
            MailAttachmentPreview.folder_name == folder,
            MailAttachmentPreview.imap_uid == int(str(uid)),
            MailAttachmentPreview.attachment_id == attachment_id,
        )
    )


def _ensure_preview_record(
    session: AuthSession,
    folder: str,
    uid: str,
    attachment: dict[str, Any],
    *,
    status_hint: str = "pending",
) -> MailAttachmentPreview | None:
    """确保数据库里存在对应附件的预览索引记录。"""
    attachment_id = str(attachment.get("attachment_id") or "")
    filename = str(attachment.get("filename") or "attachment")
    content_type = str(attachment.get("content_type") or "application/octet-stream")
    preview_kind = _preview_kind(filename, content_type)
    if not attachment_id or preview_kind is None:
        return None

    normalized_email = session.email.strip().lower()
    ensure_mail_account(normalized_email)
    session_factory = get_session_factory()
    with session_factory() as db_session:
        account, message_row = _find_message_cache_row(db_session, normalized_email, folder, uid)
        if account is None:
            return None
        record = _lookup_preview_record(db_session, normalized_email, folder, uid, attachment_id)
        storage_key = _preview_storage_key(normalized_email, folder, uid, attachment_id, filename, content_type)
        source_hash = _preview_source_hash(
            folder,
            uid,
            attachment_id,
            filename,
            content_type,
            int(attachment.get("size_bytes") or 0),
        )
        if record is None:
            record = MailAttachmentPreview(
                account_id=account.id,
                message_id=message_row.id if message_row is not None else None,
                folder_name=folder,
                imap_uid=int(str(uid)),
                attachment_id=attachment_id,
                filename=filename,
                source_content_type=content_type,
                preview_kind=preview_kind,
                storage_key=storage_key,
                source_hash=source_hash,
                status=status_hint,
                size_bytes=int(attachment.get("size_bytes") or 0),
            )
            db_session.add(record)
        else:
            record.message_id = message_row.id if message_row is not None else record.message_id
            record.filename = filename
            record.source_content_type = content_type
            record.preview_kind = preview_kind
            record.storage_key = storage_key
            record.size_bytes = int(attachment.get("size_bytes") or 0)
            if record.source_hash != source_hash and record.status == "ready":
                record.status = "pending"
            if record.status in {"failed", "pending"} and status_hint == "processing":
                record.status = "processing"
            record.source_hash = source_hash
        db_session.commit()
        db_session.refresh(record)
        return record


def _attachment_from_cached_detail(session: AuthSession, folder: str, uid: str, attachment_id: str) -> dict[str, Any] | None:
    """从详情缓存里提取指定附件元数据，避免为了状态查询再次拉完整附件内容。"""
    cached_detail = _cached_message_detail(session, folder, uid)
    if cached_detail is None:
        try:
            cached_detail = get_message_detail(session, folder, uid)
        except Exception:
            return None
    attachments = cached_detail.get("attachments") if isinstance(cached_detail.get("attachments"), list) else []
    for item in attachments:
        if isinstance(item, dict) and str(item.get("attachment_id") or "") == attachment_id:
            return item
    return None


def _preview_status_payload(record: MailAttachmentPreview | None) -> dict[str, Any]:
    """把预览索引记录转换成前端可消费的状态对象。"""
    if record is None:
        return {
            "status": "missing",
            "ready": False,
            "thumbnail_ready": False,
        }
    thumbnail_ready = False
    thumbnail_content_type: str | None = None
    thumbnail_storage_key: str | None = None
    cached_preview = _load_preview_record_payload(record)
    if cached_preview is not None:
        thumbnail_storage_key = cached_preview.get("thumbnail_storage_key") if isinstance(cached_preview.get("thumbnail_storage_key"), str) else None
        thumbnail_content_type = cached_preview.get("thumbnail_content_type") if isinstance(cached_preview.get("thumbnail_content_type"), str) else None
        if thumbnail_storage_key:
            thumb_path, thumb_meta_path = _preview_cache_path_from_storage_key(thumbnail_storage_key)
            thumbnail_ready = _load_cached_preview(thumb_path, thumb_meta_path) is not None
    return {
        "attachment_id": record.attachment_id,
        "filename": record.filename,
        "content_type": record.source_content_type,
        "preview_content_type": record.preview_content_type,
        "preview_kind": record.preview_kind,
        "status": record.status,
        "ready": record.status == "ready",
        "thumbnail_ready": thumbnail_ready,
        "thumbnail_content_type": thumbnail_content_type,
        "error_message": record.error_message,
        "generated_at": record.generated_at.isoformat() if record.generated_at else None,
        "last_accessed_at": record.last_accessed_at.isoformat() if record.last_accessed_at else None,
    }


def _load_preview_record_payload(record: MailAttachmentPreview) -> dict[str, Any] | None:
    """从索引记录关联的文件中读取预览内容。"""
    path, meta_path = _preview_cache_path_from_storage_key(record.storage_key)
    cached = _load_cached_preview(path, meta_path)
    if cached is None:
        return None
    session_factory = get_session_factory()
    with session_factory() as db_session:
        db_record = db_session.get(MailAttachmentPreview, record.id)
        if db_record is not None:
            db_record.last_accessed_at = datetime.now(timezone.utc)
            db_session.commit()
    return cached


def _preview_thumbnail_is_ready(record: MailAttachmentPreview) -> bool:
    """判断 PDF 预览记录是否已经具备可直接展示的缩略图文件。"""
    if not _attachment_supports_thumbnail(record.filename, record.source_content_type):
        return True
    path, meta_path = _preview_cache_path_from_storage_key(record.storage_key)
    cached = _load_cached_preview(path, meta_path)
    if cached is None:
        return False
    thumbnail_storage_key = cached.get("thumbnail_storage_key")
    if not isinstance(thumbnail_storage_key, str) or not thumbnail_storage_key:
        return False
    thumb_path, thumb_meta_path = _preview_cache_path_from_storage_key(thumbnail_storage_key)
    thumb_cached = _load_cached_preview(thumb_path, thumb_meta_path)
    return (
        thumb_cached is not None
        and thumb_cached.get("thumbnail_cache_version") == ATTACHMENT_THUMBNAIL_CACHE_VERSION
    )


def _preview_thumbnail_filename(filename: str, thumbnail_content_type: str) -> str:
    """按缩略图真实内容类型生成下载/预览文件名。"""
    extension = ".svg" if thumbnail_content_type == "image/svg+xml" else ".png"
    return f"{filename}.thumbnail{extension}"


def _load_preview_thumbnail_payload(record: MailAttachmentPreview) -> dict[str, Any] | None:
    """从预览记录元数据中读取已生成的缩略图文件。"""
    path, meta_path = _preview_cache_path_from_storage_key(record.storage_key)
    cached = _load_cached_preview(path, meta_path)
    if cached is None:
        return None
    thumbnail_storage_key = cached.get("thumbnail_storage_key")
    if not isinstance(thumbnail_storage_key, str) or not thumbnail_storage_key:
        return None
    thumb_path, thumb_meta_path = _preview_cache_path_from_storage_key(thumbnail_storage_key)
    thumb_cached = _load_cached_preview(thumb_path, thumb_meta_path)
    if thumb_cached is None:
        return None
    if thumb_cached.get("thumbnail_cache_version") != ATTACHMENT_THUMBNAIL_CACHE_VERSION:
        return None
    thumb_cached["filename"] = _preview_thumbnail_filename(record.filename, str(thumb_cached["content_type"]))
    return thumb_cached


def _remove_preview_records_for_messages(email: str, folder_name: str, uids: list[str]) -> None:
    """按邮箱、文件夹和 UID 清理对应邮件的预览索引及产物文件。"""
    normalized_email = email.strip().lower()
    uid_values = [int(str(uid)) for uid in uids if str(uid).isdigit()]
    if not uid_values:
        return

    session_factory = get_session_factory()
    try:
        with session_factory() as db_session:
            account = db_session.scalar(select(MailAccount).where(MailAccount.email == normalized_email))
            if account is None:
                return
            records = db_session.scalars(
                select(MailAttachmentPreview).where(
                    MailAttachmentPreview.account_id == account.id,
                    MailAttachmentPreview.folder_name == folder_name,
                    MailAttachmentPreview.imap_uid.in_(uid_values),
                )
            ).all()
            for record in records:
                path, meta_path = _preview_cache_path_from_storage_key(record.storage_key)
                preview_meta = _load_cached_preview(path, meta_path) if path.exists() and meta_path.exists() else None
                try:
                    path.unlink(missing_ok=True)
                    meta_path.unlink(missing_ok=True)
                    thumbnail_storage_key = (
                        preview_meta.get("thumbnail_storage_key")
                        if isinstance(preview_meta, dict) and isinstance(preview_meta.get("thumbnail_storage_key"), str)
                        else None
                    )
                    if thumbnail_storage_key:
                        thumb_path, thumb_meta_path = _preview_cache_path_from_storage_key(thumbnail_storage_key)
                        thumb_path.unlink(missing_ok=True)
                        thumb_meta_path.unlink(missing_ok=True)
                except OSError:
                    pass
                db_session.delete(record)
            db_session.commit()
    except Exception:
        return


def _mark_preview_record_failed(email: str, folder: str, uid: str, attachment_id: str, error_message: str) -> None:
    """把单个附件预览记录标记为失败，避免后台任务异常后状态悬挂。"""
    session_factory = get_session_factory()
    with session_factory() as db_session:
        record = _lookup_preview_record(db_session, email, folder, uid, attachment_id)
        if record is None:
            return
        record.status = "failed"
        record.error_message = error_message
        db_session.commit()


def _preview_task_key(email: str, folder: str, uid: str, attachment_id: str) -> str:
    """生成单个附件预览后台任务的去重键。"""
    return "::".join([email.strip().lower(), folder, uid, attachment_id])


def _schedule_preview_housekeeping(*, force: bool = False) -> None:
    """按周期调度一次预览缓存整理任务。"""
    settings = get_settings()
    interval = max(int(settings.attachment_preview_housekeeping_interval_seconds), 1)
    now_ts = datetime.now(timezone.utc).timestamp()
    with PREVIEW_HOUSEKEEPING_LOCK:
        if PREVIEW_HOUSEKEEPING_STATE["running"]:
            return
        if not force and now_ts - float(PREVIEW_HOUSEKEEPING_STATE["last_run_ts"]) < interval:
            return
        PREVIEW_HOUSEKEEPING_STATE["running"] = True
        PREVIEW_HOUSEKEEPING_STATE["last_run_ts"] = now_ts

    def run() -> None:
        try:
            with PREVIEW_CACHE_LOCK:
                _cleanup_preview_cache_if_needed()
        except Exception:
            return
        finally:
            with PREVIEW_HOUSEKEEPING_LOCK:
                PREVIEW_HOUSEKEEPING_STATE["running"] = False

    MAILBOX_BACKGROUND_EXECUTOR.submit(run)


def _invalidate_message_cache(email: str, folders: list[str]) -> None:
    """清理受影响文件夹的列表、搜索和详情缓存。"""
    normalized_email = email.strip().lower()
    unique_folders = [folder.strip() for folder in dict.fromkeys(folder for folder in folders if folder and folder.strip())]
    if not unique_folders:
        return

    try:
        client = redis_client.get_redis_client()
        keys: list[str] = []
        for folder in unique_folders:
            keys.extend(str(key) for key in client.scan_iter(match=f"mail:list:{normalized_email}:{folder}:*"))
            keys.extend(str(key) for key in client.scan_iter(match=f"mail:search:{normalized_email}:{folder}:*"))
            keys.extend(str(key) for key in client.scan_iter(match=f"mail:detail:{normalized_email}:{folder}:*"))
            keys.extend(str(key) for key in client.scan_iter(match=f"mail:attachment:{normalized_email}:{folder}:*"))
        if keys:
            client.delete(*keys)
    except Exception:
        return


def _parse_message_date(value: str | None) -> datetime | None:
    """把 ISO 时间字符串安全解析为 `datetime`。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed


def _datetime_to_iso(value: datetime | None) -> str | None:
    """把时间对象标准化为 ISO 字符串。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _message_matches_attachment_state(message: dict[str, Any], has_attachments: bool | None) -> bool:
    """判断邮件是否满足附件存在性筛选条件。"""
    if has_attachments is None:
        return True
    return bool(message.get("has_attachments")) is has_attachments


def _message_matches_sender(message: dict[str, Any], sender: str | None) -> bool:
    """判断邮件是否满足发件人模糊匹配条件。"""
    if not sender:
        return True
    sender_text = sender.lower()
    message_sender = message.get("sender") or {}
    sender_name = str(message_sender.get("name", "")).lower()
    sender_email = str(message_sender.get("email", "")).lower()
    return sender_text in sender_name or sender_text in sender_email


def _message_matches_date_range(message: dict[str, Any], date_from: date | None, date_to: date | None) -> bool:
    """判断邮件日期是否落在指定区间内。"""
    if date_from is None and date_to is None:
        return True
    message_date = _parse_message_date(message.get("date"))
    if message_date is None:
        return False
    current_date = message_date.date()
    if date_from and current_date < date_from:
        return False
    if date_to and current_date > date_to:
        return False
    return True


def _message_matches_query(message: dict[str, Any], query: str) -> bool:
    """判断邮件是否命中全文关键词搜索。"""
    lowered = query.lower()
    return lowered in _message_search_text(message) or lowered in str(message.get("_search_text", "")).lower()


def _cached_message_rows(session: AuthSession, folder: str, uids: list[str]) -> dict[str, dict[str, Any]]:
    """从本地数据库和详情缓存中回填已有邮件摘要。"""
    uid_values = [int(str(uid)) for uid in uids if str(uid).isdigit()]
    if not uid_values:
        return {}

    detail_cache = JsonCache(redis_client.get_redis_client())
    rows: dict[str, dict[str, Any]] = {}
    try:
        session_factory = get_session_factory()
        with session_factory() as db_session:
            account = db_session.scalar(select(MailAccount).where(MailAccount.email == session.email.strip().lower()))
            if account is None:
                raise LookupError("account-missing")
            folder_row = db_session.scalar(select(MailFolder).where(MailFolder.account_id == account.id, MailFolder.name == folder))
            if folder_row is None:
                raise LookupError("folder-missing")
            cached_messages = db_session.scalars(
                select(MailMessage).where(
                    MailMessage.account_id == account.id,
                    MailMessage.folder_id == folder_row.id,
                    MailMessage.imap_uid.in_(uid_values),
                )
            ).all()
        for message in cached_messages:
            uid_text = str(message.imap_uid)
            detail_payload = detail_cache.get(_message_detail_cache_key(session.email, folder, uid_text)) or {}
            row_payload = detail_cache.get(_message_row_cache_key(session.email, folder, uid_text)) or {}
            cached_search_text = str(
                detail_payload.get("search_text")
                or row_payload.get("_search_text")
                or ""
            )
            rows[uid_text] = {
                "uid": str(message.imap_uid),
                "message_id": message.message_id,
                "subject": str(message.subject or "(无主题)"),
                "sender": {
                    "name": str(message.sender_name or ""),
                    "email": str(message.sender_email or ""),
                },
                "to": [{"name": "", "email": email} for email in (message.to_emails or [])],
                "cc": [{"name": "", "email": email} for email in (message.cc_emails or [])],
                "date": _datetime_to_iso(message.sent_at),
                "read": bool(message.is_read),
                "has_attachments": bool(message.has_attachments),
                "attachment_types": [],
                "snippet": str(message.snippet or ""),
                "html_body": str(detail_payload.get("html_body") or row_payload.get("html_body") or ""),
                "text_body": str(detail_payload.get("text_body") or row_payload.get("text_body") or ""),
                "_search_text": cached_search_text,
            }
    except Exception:
        for uid in uids:
            uid_text = str(uid)
            cached_row = detail_cache.get(_message_row_cache_key(session.email, folder, uid_text))
            if isinstance(cached_row, dict):
                rows[uid_text] = cached_row
    return rows


def _persist_message_cache(
    session: AuthSession,
    folder: str,
    summary: dict[str, Any],
    detail: dict[str, Any],
    *,
    raw_message: bytes | None = None,
) -> None:
    """把邮件摘要和详情分别落到数据库与 Redis 缓存。"""
    normalized_email = session.email.strip().lower()
    settings = get_settings()
    try:
        session_factory = get_session_factory()
        with session_factory() as db_session:
            account = db_session.scalar(select(MailAccount).where(MailAccount.email == normalized_email))
            if account is None:
                account = MailAccount(
                    email=normalized_email,
                    display_name=None,
                    imap_host=settings.mail_imap_host,
                    imap_port=settings.mail_imap_port,
                    imap_ssl=settings.mail_imap_ssl,
                    smtp_host=settings.mail_smtp_host,
                    smtp_port=settings.mail_smtp_port,
                    smtp_ssl=settings.mail_smtp_ssl,
                )
                db_session.add(account)
                db_session.flush()
            folder_row = db_session.scalar(select(MailFolder).where(MailFolder.account_id == account.id, MailFolder.name == folder))
            if folder_row is None:
                folder_row = MailFolder(
                    account_id=account.id,
                    name=folder,
                    display_name=folder,
                    folder_type="custom",
                    delimiter="/",
                )
                db_session.add(folder_row)
                db_session.flush()

            uid_value = int(str(summary["uid"]))
            message = db_session.scalar(
                select(MailMessage).where(
                    MailMessage.account_id == account.id,
                    MailMessage.folder_id == folder_row.id,
                    MailMessage.imap_uid == uid_value,
                )
            )
            if message is None:
                message = MailMessage(account_id=account.id, folder_id=folder_row.id, imap_uid=uid_value)
                db_session.add(message)

            sender = summary.get("sender") if isinstance(summary.get("sender"), dict) else {}
            to_items = detail.get("to") if isinstance(detail.get("to"), list) else summary.get("to", [])
            cc_items = detail.get("cc") if isinstance(detail.get("cc"), list) else summary.get("cc", [])

            message.message_id = str(summary.get("message_id") or "") or None
            message.subject = str(summary.get("subject") or "")
            message.sender_name = str(sender.get("name") or "") or None
            message.sender_email = str(sender.get("email") or "") or None
            message.to_emails = [str(item.get("email") or "") for item in to_items if isinstance(item, dict) and item.get("email")]
            message.cc_emails = [str(item.get("email") or "") for item in cc_items if isinstance(item, dict) and item.get("email")]
            message.sent_at = datetime.fromisoformat(str(summary["date"])) if summary.get("date") else None
            message.received_at = message.sent_at
            message.snippet = str(summary.get("snippet") or "")
            message.has_attachments = bool(detail.get("attachments"))
            message.is_read = bool(summary.get("read"))
            message.cached_at = datetime.now(timezone.utc)
            db_session.commit()
    except Exception:
        pass
    detail_cache = JsonCache(redis_client.get_redis_client())
    uid_text = str(summary["uid"])
    html_body = str(detail.get("html_body") or "")
    text_body = str(detail.get("text_body") or "")
    detail_cache.set(
        _message_detail_cache_key(session.email, folder, uid_text),
        {
            "uid": uid_text,
            "message_id": summary.get("message_id"),
            "subject": str(summary.get("subject") or "(无主题)"),
            "from": detail.get("from") if isinstance(detail.get("from"), list) else [],
            "to": detail.get("to") if isinstance(detail.get("to"), list) else [],
            "cc": detail.get("cc") if isinstance(detail.get("cc"), list) else [],
            "date": detail.get("date"),
            "html_body": html_body,
            "text_body": text_body,
            "read": bool(detail.get("read")),
            "attachments": detail.get("attachments") if isinstance(detail.get("attachments"), list) else [],
            "search_text": re.sub(r"\s+", " ", f"{text_body} {re.sub(r'<[^>]+>', ' ', html_body)}").strip(),
            "raw_b64": b64encode(raw_message).decode("ascii") if isinstance(raw_message, (bytes, bytearray)) else "",
        },
        ttl_seconds=3600,
    )
    detail_cache.set(
        _message_row_cache_key(session.email, folder, uid_text),
        {
            "uid": uid_text,
            "message_id": summary.get("message_id"),
            "subject": str(summary.get("subject") or "(无主题)"),
            "sender": summary.get("sender") if isinstance(summary.get("sender"), dict) else {"name": "", "email": ""},
            "to": detail.get("to") if isinstance(detail.get("to"), list) else [],
            "cc": detail.get("cc") if isinstance(detail.get("cc"), list) else [],
            "date": detail.get("date") or summary.get("date"),
            "read": bool(summary.get("read")),
            "has_attachments": bool(detail.get("attachments")),
            "attachment_types": [],
            "snippet": str(summary.get("snippet") or ""),
            "html_body": html_body,
            "text_body": text_body,
            "_search_text": re.sub(r"\s+", " ", f"{text_body} {re.sub(r'<[^>]+>', ' ', html_body)}").strip(),
        },
        ttl_seconds=3600,
    )


def _page_uids(uids: list[str], page: int, page_size: int) -> list[str]:
    """按 UID 倒序切出指定分页范围。"""
    def uid_sort_key(uid: str) -> tuple[int, str]:
        return (int(uid), uid) if str(uid).isdigit() else (0, str(uid))

    offset = max(page - 1, 0) * page_size
    return sorted((str(uid) for uid in uids), key=uid_sort_key, reverse=True)[offset : offset + page_size]


def list_messages(
    session: AuthSession,
    folder: str,
    *,
    page: int = 1,
    page_size: int = 30,
    refresh: bool = False,
) -> MailboxPage:
    """读取某个文件夹的分页邮件列表，优先命中缓存。"""
    cache = JsonCache(redis_client.get_redis_client())
    key = _message_cache_key(session.email, folder, page, page_size)
    if not refresh:
        cached = cache.get(key)
        if cached:
            cached_payload = dict(cached)
            cached_messages = cached_payload.get("messages")
            if isinstance(cached_messages, list):
                cached_payload["messages"] = [
                    _message_list_item(message) for message in cached_messages if isinstance(message, dict)
                ]
            return MailboxPage(cached=True, **cached_payload)

    adapter = _connect_imap(session)
    try:
        adapter.select_folder(folder)
        uids = _uid_search(adapter, "ALL")
        page_uids = _page_uids(uids, page, page_size)
        cached_rows = _cached_message_rows(session, folder, page_uids)
        page_messages: list[dict[str, Any]] = []
        for uid in page_uids:
            row = cached_rows.get(str(uid))
            if row is None:
                row = _message_summary(uid, _uid_fetch_message_bytes(adapter, uid))
            page_messages.append(row)
        page_messages = _move_blacklisted_messages(session, adapter, folder, page_messages)
        page_messages.sort(key=lambda item: item["date"] or "", reverse=True)
        sync_message_summaries(session.email, folder, page_messages, total_count=len(uids))
        response_messages = [_message_list_item(message) for message in page_messages]
        payload = {
            "folder": folder,
            "page": page,
            "page_size": page_size,
            "total": len(uids),
            "messages": response_messages,
        }
        cache.set(key, payload, ttl_seconds=60)
        _schedule_detail_prewarm(session, folder, [message["uid"] for message in response_messages])
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
    sender: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    has_attachments: bool | None = None,
    refresh: bool = False,
) -> MailboxPage:
    """执行文件夹内全文搜索，并支持发件人、日期和附件筛选。"""
    normalized_query = query.strip()
    if not normalized_query:
        raise AppError("SEARCH_QUERY_REQUIRED", "搜索关键词不能为空", http_status=status.HTTP_422_UNPROCESSABLE_CONTENT)
    normalized_sender = sender.strip() if sender else ""
    cache = JsonCache(redis_client.get_redis_client())
    key = _search_cache_key(
        session.email,
        folder,
        normalized_query,
        page,
        page_size,
        normalized_sender.lower(),
        date_from,
        date_to,
        has_attachments,
    )
    if not refresh:
        cached = cache.get(key)
        if cached:
            return MailboxPage(cached=True, **cached)

    adapter = _connect_imap(session)
    try:
        adapter.select_folder(folder)
        uids = _uid_search(adapter, "ALL")
        cached_rows = _cached_message_rows(session, folder, uids)
        rows: list[dict[str, Any]] = []
        for uid in uids:
            row = cached_rows.get(str(uid))
            if row is None:
                raw = _uid_fetch_message_bytes(adapter, uid)
                row = _message_summary(uid, raw)
            rows.append(row)
        rows = _move_blacklisted_messages(session, adapter, folder, rows)
        sync_message_summaries(session.email, folder, rows, total_count=len(rows))
        messages = [
            row
            for row in rows
            if _message_matches_query(row, normalized_query)
            and _message_matches_sender(row, normalized_sender)
            and _message_matches_date_range(row, date_from, date_to)
            and _message_matches_attachment_state(row, has_attachments)
        ]
        messages.sort(key=lambda item: item["date"] or "", reverse=True)
        offset = max(page - 1, 0) * page_size
        response_messages = []
        for row in messages[offset : offset + page_size]:
            response_messages.append(_message_list_item(row))
        payload = {
            "folder": folder,
            "page": page,
            "page_size": page_size,
            "total": len(messages),
            "messages": response_messages,
        }
        cache.set(key, payload, ttl_seconds=60)
        _schedule_detail_prewarm(session, folder, [message["uid"] for message in response_messages])
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
    """读取单封邮件详情，并按设置自动标记已读。"""
    cached_detail = _cached_message_detail(session, folder, uid)
    if cached_detail is not None:
        return cached_detail
    adapter = _connect_imap(session)
    try:
        adapter.select_folder(folder)
        raw: bytes | None = None
        section_result = _message_detail_from_sections(adapter, uid)
        if section_result is not None:
            summary, detail = section_result
            _cache_attachment_sections_from_detail(session, adapter, folder, uid, detail)
        else:
            raw = _uid_fetch_message_bytes(adapter, uid)
            summary, detail = _build_message_detail_from_raw(uid, raw)
        system_preferences = session.preferences.get("system")
        mark_read_on_open = True
        if isinstance(system_preferences, dict):
            mark_read_on_open = bool(system_preferences.get("mark_read_on_open", True))
        if mark_read_on_open:
            summary["read"] = True
            detail["read"] = True
            _schedule_mark_message_read(session, folder, uid)
        _persist_message_cache(session, folder, summary, detail, raw_message=raw)
        _schedule_attachment_preview_generation_from_detail(session, folder, uid, detail)
        detail["attachments"] = _annotate_attachment_preview_state(
            session,
            folder,
            uid,
            detail.get("attachments") if isinstance(detail.get("attachments"), list) else [],
        )
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
    """读取单个附件的元数据和二进制内容。"""
    validate_attachment_id(attachment_id)
    cached_attachment = _load_cached_attachment(session, folder, uid, attachment_id)
    if cached_attachment is not None:
        return cached_attachment
    cached_detail = _cached_message_detail(session, folder, uid)
    if cached_detail is not None:
        attachments = cached_detail.get("attachments") if isinstance(cached_detail.get("attachments"), list) else []
        attachment_meta = next(
            (
                item for item in attachments
                if isinstance(item, dict) and str(item.get("attachment_id") or "") == attachment_id
            ),
            None,
        )
        section = str(attachment_meta.get("section") or "") if isinstance(attachment_meta, dict) else ""
        if section:
            adapter = _connect_imap(session)
            try:
                adapter.select_folder(folder)
                content = _decode_section_transfer_bytes(
                    adapter.uid_fetch_body_section(uid, section),
                    str(attachment_meta.get("encoding") or "") if isinstance(attachment_meta, dict) else "",
                )
                attachment = {
                    "attachment_id": attachment_id,
                    "filename": str(attachment_meta.get("filename") or "attachment"),
                    "content_type": str(attachment_meta.get("content_type") or "application/octet-stream"),
                    "size_bytes": int(attachment_meta.get("size_bytes") or len(content)),
                    "section": section,
                    "encoding": str(attachment_meta.get("encoding") or ""),
                    "content": content,
                }
                _cache_attachment_contents(session, folder, uid, [attachment])
                return attachment
            except Exception:
                pass
            finally:
                try:
                    adapter.logout()
                except Exception:
                    pass
    cached_raw = _load_cached_message_raw(session, folder, uid)
    if cached_raw is not None:
        message = message_from_bytes(cached_raw, policy=default)
        _, _, attachments = _body_parts(message, include_content=True)
        _cache_attachment_contents(session, folder, uid, attachments)
        for attachment in attachments:
            if attachment["attachment_id"] == attachment_id:
                return attachment
    adapter = _connect_imap(session)
    try:
        adapter.select_folder(folder)
        raw = _uid_fetch_message_bytes(adapter, uid)
        message = message_from_bytes(raw, policy=default)
        _, _, attachments = _body_parts(message, include_content=True)
        _cache_attachment_contents(session, folder, uid, attachments)
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


def _docx_preview_html(content: bytes, filename: str) -> bytes:
    """把 docx 正文提取为简单 HTML，供附件预览弹层直接展示。"""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            document_xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        raise AppError(
            "ATTACHMENT_PREVIEW_UNSUPPORTED",
            "当前附件暂不支持预览",
            http_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        ) from exc

    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError as exc:
        raise AppError(
            "ATTACHMENT_PREVIEW_UNSUPPORTED",
            "当前附件暂不支持预览",
            http_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        ) from exc

    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        text_parts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        paragraph_text = "".join(text_parts).strip()
        if paragraph_text:
            paragraphs.append(paragraph_text)

    if not paragraphs:
        paragraphs.append("该 Word 附件暂无可提取的正文内容。")

    html_body = "".join(f"<p>{html.escape(item)}</p>" for item in paragraphs)
    preview_html = (
        "<!doctype html>"
        "<html><head><meta charset='utf-8'>"
        f"<title>{html.escape(filename)}</title>"
        "<style>"
        "body{margin:0;padding:24px;font:15px/1.8 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        "color:#24324a;background:#fff;}"
        "article{max-width:880px;margin:0 auto;}"
        "h1{font-size:20px;line-height:1.4;margin:0 0 20px;font-weight:700;}"
        "p{margin:0 0 14px;white-space:pre-wrap;word-break:break-word;}"
        "</style></head><body><article>"
        f"<h1>{html.escape(filename)}</h1>{html_body}</article></body></html>"
    )
    return preview_html.encode("utf-8")


def _docx_thumbnail_svg(content: bytes, filename: str) -> bytes:
    """把 docx 前几段正文渲染成一张轻量 SVG 缩略图。"""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            document_xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        raise AppError(
            "ATTACHMENT_PREVIEW_UNSUPPORTED",
            "当前附件暂不支持预览",
            http_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        ) from exc

    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError as exc:
        raise AppError(
            "ATTACHMENT_PREVIEW_UNSUPPORTED",
            "当前附件暂不支持预览",
            http_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        ) from exc

    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        text_parts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        paragraph_text = re.sub(r"\s+", " ", "".join(text_parts)).strip()
        if paragraph_text:
            paragraphs.append(paragraph_text)

    if not paragraphs:
        paragraphs.append("该 Word 附件暂无可提取的正文内容。")

    def truncate_for_svg(value: str, max_width: int) -> str:
        current = ""
        width = 0
        for char in value:
            char_width = 2 if ord(char) > 127 else 1
            if width + char_width > max_width:
                return current.rstrip() + "..."
            current += char
            width += char_width
        return current

    def wrap_for_svg(value: str, max_width: int) -> list[str]:
        lines: list[str] = []
        current = ""
        width = 0
        for char in value:
            char_width = 2 if ord(char) > 127 else 1
            if current and width + char_width > max_width:
                lines.append(current.rstrip())
                current = char
                width = char_width
                continue
            current += char
            width += char_width
        if current.strip():
            lines.append(current.rstrip())
        return lines

    preview_lines: list[str] = []
    for paragraph in paragraphs:
        preview_lines.extend(wrap_for_svg(paragraph, 56))
        if len(preview_lines) >= 13:
            break

    safe_filename = html.escape(truncate_for_svg(filename, 52))
    safe_lines = [html.escape(item) for item in preview_lines[:13]]
    svg_lines = "".join(
        f"<text x='112' y='{402 + index * 42}' font-size='22' fill='#1f2937'>{line}</text>"
        for index, line in enumerate(safe_lines)
    )
    return (
        "<svg xmlns='http://www.w3.org/2000/svg' width='960' height='1280' viewBox='0 0 960 1280'>"
        "<defs>"
        "<linearGradient id='bg' x1='0' y1='0' x2='0' y2='1'>"
        "<stop offset='0%' stop-color='#f8fbff'/>"
        "<stop offset='100%' stop-color='#eef4ff'/>"
        "</linearGradient>"
        "<filter id='shadow' x='-20%' y='-20%' width='140%' height='140%'>"
        "<feDropShadow dx='0' dy='20' stdDeviation='24' flood-color='#c7d2fe' flood-opacity='0.25'/>"
        "</filter>"
        "<clipPath id='bodyClip'><rect x='96' y='370' width='768' height='700' rx='18'/></clipPath>"
        "</defs>"
        "<rect width='960' height='1280' rx='40' fill='url(#bg)'/>"
        "<rect x='72' y='72' width='816' height='180' rx='28' fill='#dbeafe' filter='url(#shadow)'/>"
        "<text x='112' y='164' font-size='48' font-weight='700' fill='#1d4ed8'>DOCX</text>"
        f"<text x='112' y='214' font-size='24' fill='#475569'>{safe_filename}</text>"
        "<rect x='72' y='280' width='816' height='856' rx='32' fill='#ffffff' stroke='#dbe3f0'/>"
        "<text x='88' y='344' font-size='22' font-weight='600' fill='#64748b'>正文预览</text>"
        f"<g clip-path='url(#bodyClip)'>{svg_lines}</g>"
        "</svg>"
    ).encode("utf-8")


def _pdf_thumbnail_png(content: bytes) -> bytes:
    """使用 PyMuPDF 渲染 PDF 首屏缩略图。"""
    if fitz is None:
        raise AppError(
            "ATTACHMENT_THUMBNAIL_UNAVAILABLE",
            "当前环境缺少 PDF 缩略图依赖",
            http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    with fitz.open(stream=content, filetype="pdf") as document:
        if document.page_count <= 0:
            raise AppError(
                "ATTACHMENT_PREVIEW_FAILED",
                "PDF 预览生成失败",
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        page = document.load_page(0)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(0.45, 0.45), alpha=False)
        return pixmap.tobytes("png")


def _build_preview_record_payload(session: AuthSession, folder: str, uid: str, attachment_id: str) -> dict[str, Any]:
    """同步构建并持久化单个附件的预览产物。"""
    attachment = get_message_attachment(session, folder, uid, attachment_id)
    filename = str(attachment["filename"])
    content_type = str(attachment["content_type"] or "application/octet-stream")
    content = bytes(attachment["content"])
    preview_kind = _preview_kind(filename, content_type)
    if preview_kind is None:
        raise AppError(
            "ATTACHMENT_PREVIEW_UNSUPPORTED",
            "当前附件暂不支持预览",
            http_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    preview_content = content
    preview_content_type = content_type
    thumbnail_content: bytes | None = None
    thumbnail_content_type: str | None = None
    if (
        content_type.lower() == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or filename.lower().endswith(".docx")
    ):
        preview_content = _docx_preview_html(content, filename)
        preview_content_type = "text/html; charset=utf-8"
        thumbnail_content = _docx_thumbnail_svg(content, filename)
        thumbnail_content_type = "image/svg+xml"
    elif content_type.lower() == "application/pdf" or filename.lower().endswith(".pdf"):
        try:
            thumbnail_content = _pdf_thumbnail_png(content)
            thumbnail_content_type = "image/png"
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "ATTACHMENT_THUMBNAIL_FAILED",
                f"PDF 缩略图生成失败: {exc.__class__.__name__}",
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            ) from exc

    attachment_meta = {
        "attachment_id": attachment_id,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": int(attachment.get("size_bytes") or len(content)),
    }
    record = _ensure_preview_record(session, folder, uid, attachment_meta, status_hint="processing")
    if record is None:
        raise AppError(
            "ATTACHMENT_PREVIEW_UNSUPPORTED",
            "当前附件暂不支持预览",
            http_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )
    path, meta_path = _preview_cache_path_from_storage_key(record.storage_key)
    thumbnail_storage_key: str | None = None
    if thumbnail_content is not None and thumbnail_content_type is not None:
        thumbnail_storage_key = _preview_thumbnail_storage_key(
            session.email,
            folder,
            uid,
            attachment_id,
            filename,
            content_type,
            thumbnail_content_type,
        )
        thumb_path, thumb_meta_path = _preview_cache_path_from_storage_key(thumbnail_storage_key)
        thumbnail_filename = _preview_thumbnail_filename(filename, thumbnail_content_type)

    with PREVIEW_CACHE_LOCK:
        try:
            if thumbnail_content is not None and thumbnail_content_type is not None and thumbnail_storage_key is not None:
                _persist_preview_cache(
                    thumb_path,
                    thumb_meta_path,
                    filename=thumbnail_filename,
                    content_type=thumbnail_content_type,
                    content=thumbnail_content,
                    thumbnail_cache_version=ATTACHMENT_THUMBNAIL_CACHE_VERSION,
                )
            _persist_preview_cache(
                path,
                meta_path,
                filename=filename,
                content_type=preview_content_type,
                content=preview_content,
                thumbnail_storage_key=thumbnail_storage_key,
                thumbnail_content_type=thumbnail_content_type,
            )
        except OSError as exc:
            session_factory = get_session_factory()
            with session_factory() as db_session:
                db_record = db_session.get(MailAttachmentPreview, record.id)
                if db_record is not None:
                    db_record.status = "failed"
                    db_record.error_message = f"预览文件写入失败: {exc.__class__.__name__}"
                    db_session.commit()
            raise

    session_factory = get_session_factory()
    with session_factory() as db_session:
        db_record = db_session.get(MailAttachmentPreview, record.id)
        if db_record is not None:
            now = datetime.now(timezone.utc)
            db_record.status = "ready"
            db_record.preview_content_type = preview_content_type
            db_record.preview_size_bytes = len(preview_content)
            db_record.generated_at = now
            db_record.last_accessed_at = now
            db_record.error_message = None
            db_session.commit()
            db_session.refresh(db_record)
            record = db_record

    _schedule_preview_housekeeping(force=True)
    return {
        "content": preview_content,
        "content_type": preview_content_type,
        "filename": filename,
        "thumbnail_content_type": thumbnail_content_type,
        "thumbnail_storage_key": thumbnail_storage_key,
    }


def get_message_attachment_preview_status(session: AuthSession, folder: str, uid: str, attachment_id: str) -> dict[str, Any]:
    """返回附件预览当前状态，并在需要时异步触发生成。"""
    validate_attachment_id(attachment_id)
    session_factory = get_session_factory()
    with session_factory() as db_session:
        existing_record = _lookup_preview_record(db_session, session.email.strip().lower(), folder, uid, attachment_id)
        if existing_record is not None and existing_record.status == "ready":
            path, _meta_path = _preview_cache_path_from_storage_key(existing_record.storage_key)
            if path.exists() and _preview_thumbnail_is_ready(existing_record):
                return _preview_status_payload(existing_record)
            existing_record.status = "pending"
            existing_record.error_message = (
                "预览文件缺失，已重新排队生成"
                if not path.exists()
                else "附件缩略图缺失，已重新排队生成"
            )
            db_session.commit()
            db_session.refresh(existing_record)

    attachment = _attachment_from_cached_detail(session, folder, uid, attachment_id)
    if attachment is None:
        raise AppError(
            "ATTACHMENT_NOT_FOUND",
            "附件不存在",
            http_status=status.HTTP_404_NOT_FOUND,
        )

    filename = str(attachment.get("filename") or "attachment")
    content_type = str(attachment.get("content_type") or "application/octet-stream")
    preview_kind = _preview_kind(filename, content_type)
    if preview_kind is None:
        return {
            "attachment_id": attachment_id,
            "filename": filename,
            "content_type": content_type,
            "preview_kind": None,
            "status": "unsupported",
            "ready": False,
            "error_message": "当前附件类型暂不支持预览",
        }

    record = _ensure_preview_record(session, folder, uid, attachment, status_hint="pending")
    if record is None:
        return {
            "attachment_id": attachment_id,
            "filename": filename,
            "content_type": content_type,
            "preview_kind": preview_kind,
            "status": "pending",
            "ready": False,
        }

    payload = _preview_status_payload(record)
    if payload["status"] == "ready":
        path, _meta_path = _preview_cache_path_from_storage_key(record.storage_key)
        if path.exists() and (not _attachment_supports_thumbnail(record.filename, record.source_content_type) or bool(payload.get("thumbnail_ready"))):
            return payload
        with session_factory() as db_session:
            db_record = db_session.get(MailAttachmentPreview, record.id)
            if db_record is not None:
                db_record.status = "pending"
                db_record.error_message = (
                    "预览文件缺失，已重新排队生成"
                    if not path.exists()
                    else "附件缩略图缺失，已重新排队生成"
                )
                db_session.commit()

    _schedule_attachment_preview_generation(session, folder, uid, attachment)
    with session_factory() as db_session:
        refreshed = db_session.get(MailAttachmentPreview, record.id)
        if refreshed is not None:
            return _preview_status_payload(refreshed)
    return payload


def get_message_attachment_preview(session: AuthSession, folder: str, uid: str, attachment_id: str) -> dict[str, Any]:
    """按可预览类型返回附件预览内容。"""
    validate_attachment_id(attachment_id)
    status_payload = get_message_attachment_preview_status(session, folder, uid, attachment_id)
    if status_payload.get("status") == "ready":
        session_factory = get_session_factory()
        with session_factory() as db_session:
            record = _lookup_preview_record(db_session, session.email, folder, uid, attachment_id)
            if record is not None:
                cached_preview = _load_preview_record_payload(record)
                if cached_preview is not None:
                    return cached_preview
    return _build_preview_record_payload(session, folder, uid, attachment_id)


def get_message_attachment_preview_thumbnail(session: AuthSession, folder: str, uid: str, attachment_id: str) -> dict[str, Any]:
    """读取附件预览缩略图，没有则先同步触发预览构建。"""
    validate_attachment_id(attachment_id)
    session_factory = get_session_factory()
    with session_factory() as db_session:
        record = _lookup_preview_record(db_session, session.email.strip().lower(), folder, uid, attachment_id)
        if record is not None:
            cached_thumbnail = _load_preview_thumbnail_payload(record)
            if cached_thumbnail is not None:
                return cached_thumbnail
    get_message_attachment_preview(session, folder, uid, attachment_id)
    with session_factory() as db_session:
        record = _lookup_preview_record(db_session, session.email.strip().lower(), folder, uid, attachment_id)
        if record is not None:
            cached_thumbnail = _load_preview_thumbnail_payload(record)
            if cached_thumbnail is not None:
                return cached_thumbnail
    raise AppError(
        "ATTACHMENT_THUMBNAIL_NOT_READY",
        "附件缩略图暂未准备完成",
        http_status=status.HTTP_425_TOO_EARLY,
    )


def operate_messages(session: AuthSession, folder: str, payload: MessageOperationRequest) -> dict[str, Any]:
    """对一组邮件执行已读、未读、删除、移动或星标操作。"""
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
            _sync_folder_snapshot(session.email, adapter)
            _invalidate_message_cache(session.email, [folder])
        elif payload.action == "mark_unread":
            persist_message_read_state(session.email, folder, payload.uids, is_read=False)
            _sync_folder_snapshot(session.email, adapter)
            _invalidate_message_cache(session.email, [folder])
        if payload.action in {"delete", "move"}:
            adapter.expunge()
            _remove_message_records(session.email, folder, payload.uids)
            _sync_folder_snapshot(session.email, adapter)
            affected_folders = [folder]
            if payload.action == "delete":
                affected_folders.append(trash_folder)
            elif target_folder is not None:
                affected_folders.append(target_folder)
            _invalidate_message_cache(session.email, affected_folders)
            _schedule_preview_housekeeping(force=True)
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
