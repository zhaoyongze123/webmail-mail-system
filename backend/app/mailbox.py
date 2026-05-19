"""收信、读信、搜索与邮件操作核心逻辑。

这个模块负责 IMAP 文件夹映射、邮件正文与附件解析、富文本安全清洗、
列表/详情缓存，以及邮件批量操作。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.policy import default
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

import bleach
import tinycss2
from fastapi import status
from premailer import transform
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select

from app.auth import AuthSession
from app.cache import JsonCache
from app.config import get_settings
from app.contacts import list_blacklisted_contacts, list_whitelisted_contacts
from app.errors import AppError
from app import mail_adapters, redis_client
from app.mail_adapters import ImapSettings, MailAdapterError, _parse_status_response
from app.db import get_session_factory
from app.models import MailAccount, MailFolder, MailMessage
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
    session_factory = get_session_factory()
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
        "read": True,
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

    try:
        session_factory = get_session_factory()
        with session_factory() as db_session:
            account = db_session.scalar(select(MailAccount).where(MailAccount.email == session.email.strip().lower()))
            if account is None:
                return {}
            folder_row = db_session.scalar(select(MailFolder).where(MailFolder.account_id == account.id, MailFolder.name == folder))
            if folder_row is None:
                return {}
            cached_messages = db_session.scalars(
                select(MailMessage).where(
                    MailMessage.account_id == account.id,
                    MailMessage.folder_id == folder_row.id,
                    MailMessage.imap_uid.in_(uid_values),
                )
            ).all()
    except Exception:
        return {}

    detail_cache = JsonCache(redis_client.get_redis_client())
    rows: dict[str, dict[str, Any]] = {}
    for message in cached_messages:
        uid_text = str(message.imap_uid)
        detail_payload = detail_cache.get(_message_detail_cache_key(session.email, folder, uid_text)) or {}
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
            "html_body": str(detail_payload.get("html_body") or ""),
            "text_body": str(detail_payload.get("text_body") or ""),
            "_search_text": str(detail_payload.get("search_text") or ""),
        }
    return rows


def _persist_message_cache(session: AuthSession, folder: str, summary: dict[str, Any], detail: dict[str, Any]) -> None:
    """把邮件摘要和详情分别落到数据库与 Redis 缓存。"""
    normalized_email = session.email.strip().lower()
    settings = get_settings()
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
    detail_cache = JsonCache(redis_client.get_redis_client())
    uid_text = str(summary["uid"])
    html_body = str(detail.get("html_body") or "")
    text_body = str(detail.get("text_body") or "")
    detail_cache.set(
        _message_detail_cache_key(session.email, folder, uid_text),
        {
            "html_body": html_body,
            "text_body": text_body,
            "search_text": re.sub(r"\s+", " ", f"{text_body} {re.sub(r'<[^>]+>', ' ', html_body)}").strip(),
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
    adapter = _connect_imap(session)
    try:
        adapter.select_folder(folder)
        raw = _uid_fetch_message_bytes(adapter, uid)
        summary = _message_summary(uid, raw)
        detail = _message_detail(uid, raw)
        system_preferences = session.preferences.get("system")
        mark_read_on_open = True
        if isinstance(system_preferences, dict):
            mark_read_on_open = bool(system_preferences.get("mark_read_on_open", True))
        if mark_read_on_open:
            adapter.mark_seen(uid)
            persist_message_read_state(session.email, folder, [uid], is_read=True)
            summary["read"] = True
            _sync_folder_snapshot(session.email, adapter)
            _invalidate_message_cache(session.email, [folder])
            detail["read"] = True
        _persist_message_cache(session, folder, summary, detail)
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
            _sync_folder_snapshot(session.email, adapter)
            affected_folders = [folder]
            if payload.action == "delete":
                affected_folders.append(trash_folder)
            elif target_folder is not None:
                affected_folders.append(target_folder)
            _invalidate_message_cache(session.email, affected_folders)
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
