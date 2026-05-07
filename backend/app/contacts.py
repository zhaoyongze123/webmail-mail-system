from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app import redis_client
from app.auth import AuthSession


RECENT_CONTACT_LIMIT = 50
AUTOCOMPLETE_LIMIT = 10


@dataclass(frozen=True)
class ContactItem:
    email: str
    last_used_at: str

    def as_dict(self) -> dict[str, str]:
        return {"email": self.email, "last_used_at": self.last_used_at}


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _recent_contacts_key(email: str) -> str:
    return f"contacts:recent:{_normalize_email(email)}"


def _contact_used_at() -> datetime:
    return datetime.now(timezone.utc)


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
