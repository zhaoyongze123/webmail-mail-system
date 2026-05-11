from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID as UUIDType

from fastapi import status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import redis_client
from app.auth import AuthSession
from app.config import get_settings
from app.db import get_session_factory
from app.errors import AppError
from app.models import MailAccount, MailContact, MailContactTag
from app.schemas import (
    ContactCreateRequest,
    ContactListResponse,
    ContactResponse,
    ContactSearchResponse,
    ContactTagItem,
    ContactUpdateRequest,
)


RECENT_CONTACT_LIMIT = 50
AUTOCOMPLETE_LIMIT = 10
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class ContactItem:
    email: str
    last_used_at: str

    def as_dict(self) -> dict[str, str]:
        return {"email": self.email, "last_used_at": self.last_used_at}


@dataclass(frozen=True)
class ContactRuleState:
    is_blacklisted: bool = False
    is_whitelisted: bool = False

    @property
    def effective_rule(self) -> str:
        if self.is_whitelisted:
            return "whitelist"
        if self.is_blacklisted:
            return "blacklist"
        return "normal"


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_tag(value: str) -> str:
    return value.strip()


def _recent_contacts_key(email: str) -> str:
    return f"contacts:recent:{_normalize_email(email)}"


def _contact_used_at() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _session_scope():
    return get_session_factory()


def _get_account(db_session, email: str) -> MailAccount | None:
    normalized_email = _normalize_email(email)
    return db_session.scalar(select(MailAccount).where(MailAccount.email == normalized_email))


def _ensure_account(db_session, email: str) -> MailAccount:
    settings = get_settings()
    normalized_email = _normalize_email(email)
    account = db_session.scalar(select(MailAccount).where(MailAccount.email == normalized_email))
    if account is None:
        account = MailAccount(
            email=normalized_email,
            imap_host=settings.mail_imap_host,
            imap_port=settings.mail_imap_port,
            imap_ssl=settings.mail_imap_ssl,
            smtp_host=settings.mail_smtp_host,
            smtp_port=settings.mail_smtp_port,
            smtp_ssl=settings.mail_smtp_ssl,
        )
        db_session.add(account)
        db_session.flush()
    return account


def _contact_tag_items(contact: MailContact) -> list[ContactTagItem]:
    return [
        ContactTagItem(name=tag.name, created_at=tag.created_at.isoformat() if tag.created_at else "")
        for tag in sorted(contact.tags, key=lambda item: item.name)
    ]


def _contact_response(contact: MailContact) -> ContactResponse:
    return ContactResponse(
        id=str(contact.id),
        email=contact.email,
        display_name=contact.display_name,
        group_name=contact.group_name,
        company=contact.company,
        phone=contact.phone,
        notes=contact.notes,
        is_favorite=bool(contact.is_favorite),
        is_blacklisted=bool(contact.is_blacklisted),
        is_whitelisted=bool(contact.is_whitelisted),
        source=contact.source,
        use_count=int(contact.use_count),
        last_used_at=contact.last_used_at.isoformat() if contact.last_used_at else None,
        created_at=contact.created_at.isoformat() if contact.created_at else "",
        updated_at=contact.updated_at.isoformat() if contact.updated_at else "",
        tags=_contact_tag_items(contact),
    )


def _contact_rule_state(contact: MailContact | None) -> ContactRuleState:
    if contact is None:
        return ContactRuleState()
    return ContactRuleState(
        is_blacklisted=bool(contact.is_blacklisted),
        is_whitelisted=bool(contact.is_whitelisted),
    )


def _contact_query(db_session, account_id: UUIDType, query: str | None = None):
    stmt = select(MailContact).where(MailContact.account_id == account_id)
    query_text = _normalize_text(query)
    if query_text:
        like = f"%{query_text.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(MailContact.email).like(like),
                func.lower(func.coalesce(MailContact.display_name, "")).like(like),
                func.lower(func.coalesce(MailContact.group_name, "")).like(like),
                func.lower(func.coalesce(MailContact.company, "")).like(like),
                func.lower(func.coalesce(MailContact.phone, "")).like(like),
                func.lower(func.coalesce(MailContact.notes, "")).like(like),
            )
        )
    return stmt


def _upsert_tags(db_session, contact: MailContact, tags: list[str]) -> None:
    normalized = []
    seen = set()
    for tag in tags:
        clean = _normalize_tag(tag)
        if not clean:
            continue
        lowered = clean.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(clean)

    existing_by_name = {tag.name.lower(): tag for tag in contact.tags}
    desired_lower = {tag.lower() for tag in normalized}
    for tag_name, tag in list(existing_by_name.items()):
        if tag_name not in desired_lower:
            db_session.delete(tag)

    for tag_name in normalized:
        if tag_name.lower() in existing_by_name:
            continue
        db_session.add(MailContactTag(contact_id=contact.id, name=tag_name))


def _safe_commit(operation: str, callback) -> Any | None:
    try:
        session_factory = _session_scope()
        with session_factory() as db_session:
            result = callback(db_session)
            db_session.commit()
            return result
    except SQLAlchemyError:
        if operation:
            raise
    return None


def get_contact_rule_state(session: AuthSession, email: str) -> dict[str, bool | str]:
    normalized_email = _normalize_email(email)
    default_state = ContactRuleState()
    default_result = {
        "is_blacklisted": default_state.is_blacklisted,
        "is_whitelisted": default_state.is_whitelisted,
        "effective_rule": default_state.effective_rule,
    }
    if not normalized_email:
        return default_result

    def read(db_session):
        account = _get_account(db_session, session.email)
        if account is None:
            return default_result
        contact = db_session.scalar(
            select(MailContact).where(
                MailContact.account_id == account.id,
                MailContact.email == normalized_email,
            )
        )
        state = _contact_rule_state(contact)
        return {
            "is_blacklisted": state.is_blacklisted,
            "is_whitelisted": state.is_whitelisted,
            "effective_rule": state.effective_rule,
        }

    return _safe_commit("get_contact_rule_state", read) or default_result


def record_recent_contacts(session: AuthSession, recipients: list[str]) -> None:
    recent_contacts_key = _recent_contacts_key(session.email)
    redis = redis_client.get_redis_client()
    used_at = _contact_used_at()

    normalized_recipients = {_normalize_email(item) for item in recipients if str(item).strip()}
    if not normalized_recipients:
        return

    mapping = {email: used_at.timestamp() for email in normalized_recipients}
    redis.zadd(recent_contacts_key, mapping)
    excess = int(redis.zcard(recent_contacts_key)) - RECENT_CONTACT_LIMIT
    if excess > 0:
        redis.zremrangebyrank(recent_contacts_key, 0, excess - 1)
    try:
        upsert_contacts_from_recipients(session, list(normalized_recipients))
    except SQLAlchemyError:
        return


def upsert_contact_from_recipient(
    session: AuthSession,
    recipient: str,
    *,
    source: str = "sent",
    display_name: str | None = None,
    is_blacklisted: bool | None = None,
    is_whitelisted: bool | None = None,
) -> ContactResponse | None:
    email = _normalize_email(recipient)
    if not email:
        return None

    def write(db_session):
        account = _ensure_account(db_session, session.email)
        contact = db_session.scalar(
            select(MailContact).where(MailContact.account_id == account.id, MailContact.email == email)
        )
        used_at = _now()
        if contact is None:
            contact = MailContact(
                account_id=account.id,
                email=email,
                display_name=_normalize_text(display_name),
                source=source,
                use_count=1,
                last_used_at=used_at,
                is_blacklisted=bool(is_blacklisted) if is_blacklisted is not None else False,
                is_whitelisted=bool(is_whitelisted) if is_whitelisted is not None else False,
            )
            db_session.add(contact)
            db_session.flush()
        else:
            contact.use_count = int(contact.use_count) + 1
            contact.last_used_at = used_at
            contact.source = contact.source or source
            if display_name and not contact.display_name:
                contact.display_name = _normalize_text(display_name)
            if is_blacklisted is not None:
                contact.is_blacklisted = bool(is_blacklisted)
            if is_whitelisted is not None:
                contact.is_whitelisted = bool(is_whitelisted)
        db_session.flush()
        db_session.refresh(contact)
        return _contact_response(contact)

    return _safe_commit("upsert_contact_from_recipient", write)


def sync_contacts_from_recipients(session: AuthSession, recipients: list[str]) -> list[ContactResponse]:
    synced: list[ContactResponse] = []
    for recipient in recipients:
        contact = upsert_contact_from_recipient(session, recipient)
        if contact is not None:
            synced.append(contact)
    return synced


def search_recent_contacts(session: AuthSession, query: str | None = None, limit: int = AUTOCOMPLETE_LIMIT) -> list[dict[str, Any]]:
    redis = redis_client.get_redis_client()
    key = _recent_contacts_key(session.email)
    query_text = (query or "").strip().lower()
    max_limit = max(1, min(limit, AUTOCOMPLETE_LIMIT))

    raw_contacts = redis.zrevrange(key, 0, RECENT_CONTACT_LIMIT - 1, withscores=True)
    contacts: list[ContactItem] = []
    for member, score in raw_contacts:
        email = _normalize_email(str(member))
        if query_text and query_text not in email:
            continue
        contacts.append(
            ContactItem(
                email=email,
                last_used_at=datetime.fromtimestamp(float(score), tz=timezone.utc).isoformat(),
            )
        )
        if len(contacts) >= max_limit:
            break
    return [item.as_dict() for item in contacts]


def list_contacts(
    session: AuthSession,
    query: str | None = None,
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    group_name: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    def read(db_session):
        account = _get_account(db_session, session.email)
        if account is None:
            return ContactListResponse(page=page, page_size=page_size, total=0, items=[]).model_dump()

        stmt = _contact_query(db_session, account.id, query)
        if group_name is not None:
            stmt = stmt.where(MailContact.group_name == _normalize_text(group_name))
        if tag is not None:
            normalized_tag = _normalize_tag(tag)
            stmt = stmt.join(MailContact.tags).where(func.lower(MailContactTag.name) == normalized_tag.lower())

        count_stmt = stmt.with_only_columns(func.count(func.distinct(MailContact.id))).order_by(None)
        total = int(db_session.scalar(count_stmt) or 0)
        items = list(
            db_session.scalars(
                stmt.options()
                .order_by(
                    MailContact.is_favorite.desc(),
                    MailContact.last_used_at.desc().nullslast(),
                    MailContact.updated_at.desc(),
                    MailContact.email.asc(),
                )
                .offset(max(page - 1, 0) * page_size)
                .limit(page_size)
            ).all()
        )
        return ContactListResponse(
            page=page,
            page_size=page_size,
            total=total,
            items=[_contact_response(contact) for contact in items],
        ).model_dump()

    result = _safe_commit("list_contacts", read) or {"page": page, "page_size": page_size, "total": 0, "items": []}
    if "contacts" not in result:
        result["contacts"] = result.get("items", [])
    return result


def get_contact(session: AuthSession, contact_id: str) -> dict[str, Any]:
    def read(db_session):
        account = _get_account(db_session, session.email)
        if account is None:
            raise AppError("ACCOUNT_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
        try:
            contact_uuid = UUIDType(contact_id)
        except ValueError as exc:
            raise AppError("CONTACT_NOT_FOUND", "联系人不存在", http_status=status.HTTP_404_NOT_FOUND) from exc
        contact = db_session.get(MailContact, contact_uuid)
        if contact is None or contact.account_id != account.id:
            raise AppError("CONTACT_NOT_FOUND", "联系人不存在", http_status=status.HTTP_404_NOT_FOUND)
        return {"contact": _contact_response(contact).model_dump()}

    return _safe_commit("get_contact", read)


def create_contact(session: AuthSession, payload: ContactCreateRequest) -> dict[str, Any]:
    def write(db_session):
        account = _ensure_account(db_session, session.email)
        existing = db_session.scalar(
            select(MailContact).where(
                MailContact.account_id == account.id,
                MailContact.email == _normalize_email(payload.email),
            )
        )
        if existing is not None:
            raise AppError("CONTACT_ALREADY_EXISTS", "联系人已存在", http_status=status.HTTP_409_CONFLICT)
        contact = MailContact(
            account_id=account.id,
            email=_normalize_email(payload.email),
            display_name=_normalize_text(payload.display_name),
            group_name=_normalize_text(payload.group_name),
            company=_normalize_text(payload.company),
            phone=_normalize_text(payload.phone),
            notes=_normalize_text(payload.notes),
            is_favorite=bool(payload.is_favorite),
            is_blacklisted=bool(payload.is_blacklisted),
            is_whitelisted=bool(payload.is_whitelisted),
            source="manual",
            use_count=0,
            last_used_at=None,
        )
        db_session.add(contact)
        db_session.flush()
        _upsert_tags(db_session, contact, payload.tags)
        db_session.flush()
        db_session.refresh(contact)
        return {"contact": _contact_response(contact).model_dump()}

    return _safe_commit("create_contact", write)


def update_contact(session: AuthSession, contact_id: str, payload: ContactUpdateRequest) -> dict[str, Any]:
    def write(db_session):
        account = _get_account(db_session, session.email)
        if account is None:
            raise AppError("ACCOUNT_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
        try:
            contact_uuid = UUIDType(contact_id)
        except ValueError as exc:
            raise AppError("CONTACT_NOT_FOUND", "联系人不存在", http_status=status.HTTP_404_NOT_FOUND) from exc
        contact = db_session.get(MailContact, contact_uuid)
        if contact is None or contact.account_id != account.id:
            raise AppError("CONTACT_NOT_FOUND", "联系人不存在", http_status=status.HTTP_404_NOT_FOUND)

        if payload.display_name is not None:
            contact.display_name = _normalize_text(payload.display_name)
        if payload.group_name is not None:
            contact.group_name = _normalize_text(payload.group_name)
        if payload.company is not None:
            contact.company = _normalize_text(payload.company)
        if payload.phone is not None:
            contact.phone = _normalize_text(payload.phone)
        if payload.notes is not None:
            contact.notes = _normalize_text(payload.notes)
        if payload.is_favorite is not None:
            contact.is_favorite = bool(payload.is_favorite)
        if payload.is_blacklisted is not None:
            contact.is_blacklisted = bool(payload.is_blacklisted)
        if payload.is_whitelisted is not None:
            contact.is_whitelisted = bool(payload.is_whitelisted)
        if payload.tags is not None:
            _upsert_tags(db_session, contact, payload.tags)
        db_session.flush()
        db_session.refresh(contact)
        return {"contact": _contact_response(contact).model_dump()}

    return _safe_commit("update_contact", write)


def delete_contact(session: AuthSession, contact_id: str) -> dict[str, Any]:
    def write(db_session):
        account = _get_account(db_session, session.email)
        if account is None:
            raise AppError("ACCOUNT_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
        try:
            contact_uuid = UUIDType(contact_id)
        except ValueError as exc:
            raise AppError("CONTACT_NOT_FOUND", "联系人不存在", http_status=status.HTTP_404_NOT_FOUND) from exc
        contact = db_session.get(MailContact, contact_uuid)
        if contact is None or contact.account_id != account.id:
            raise AppError("CONTACT_NOT_FOUND", "联系人不存在", http_status=status.HTTP_404_NOT_FOUND)
        db_session.delete(contact)
        db_session.flush()
        return {"deleted": True, "contact_id": contact_id}

    return _safe_commit("delete_contact", write)


def search_contacts(
    session: AuthSession,
    query: str | None = None,
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    group_name: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    return list_contacts(session, query, page=page, page_size=page_size, group_name=group_name, tag=tag)


def search_contacts_for_autocomplete(
    session: AuthSession,
    query: str | None = None,
    limit: int = AUTOCOMPLETE_LIMIT,
) -> ContactSearchResponse:
    recent_contacts = search_recent_contacts(session, query=query, limit=limit)
    try:
        page_data = search_contacts(session, query=query, page=1, page_size=limit)
    except SQLAlchemyError:
        page_data = {"contacts": [], "items": []}
    combined: dict[str, dict[str, Any]] = {}
    for item in page_data.get("contacts", []) or page_data.get("items", []):
        if isinstance(item, dict) and item.get("email"):
            combined[str(item["email"]).lower()] = item
    for item in recent_contacts:
        combined[str(item["email"]).lower()] = item
    contacts = list(combined.values())[: max(1, min(limit, AUTOCOMPLETE_LIMIT))]
    return ContactSearchResponse(query=query or "", contacts=contacts)


def upsert_contacts_from_recipients(session: AuthSession, recipients: list[str]) -> None:
    if not recipients:
        return
    try:
        sync_contacts_from_recipients(session, recipients)
    except SQLAlchemyError:
        return


def list_blacklisted_contacts(session: AuthSession) -> list[str]:
    try:
        def read(db_session):
            account = _get_account(db_session, session.email)
            if account is None:
                return []
            rows = db_session.scalars(
                select(MailContact.email).where(
                    MailContact.account_id == account.id,
                    MailContact.is_blacklisted.is_(True),
                )
            ).all()
            return [_normalize_email(str(email)) for email in rows if str(email).strip()]

        result = _safe_commit("list_blacklisted_contacts", read)
        return list(result or [])
    except SQLAlchemyError:
        return []


def list_whitelisted_contacts(session: AuthSession) -> list[str]:
    try:
        def read(db_session):
            account = _get_account(db_session, session.email)
            if account is None:
                return []
            rows = db_session.scalars(
                select(MailContact.email).where(
                    MailContact.account_id == account.id,
                    MailContact.is_whitelisted.is_(True),
                )
            ).all()
            return [_normalize_email(str(email)) for email in rows if str(email).strip()]

        result = _safe_commit("list_whitelisted_contacts", read)
        return list(result or [])
    except SQLAlchemyError:
        return []


def is_blacklisted_email(session: AuthSession, email: str) -> bool:
    normalized_email = _normalize_email(email)
    if not normalized_email:
        return False

    try:
        def read(db_session):
            account = _get_account(db_session, session.email)
            if account is None:
                return False
            contact = db_session.scalar(
                select(MailContact).where(
                    MailContact.account_id == account.id,
                    MailContact.email == normalized_email,
                    MailContact.is_blacklisted.is_(True),
                )
            )
            return contact is not None

        return bool(_safe_commit("is_blacklisted_email", read))
    except SQLAlchemyError:
        return False
