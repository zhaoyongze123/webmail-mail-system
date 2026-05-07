from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any

from fastapi import status
from pydantic import BaseModel, Field

from app import mail_adapters, redis_client
from app.attachments import load_temp_attachment
from app.auth import AuthSession
from app.compose import _imap_settings
from app.errors import AppError
from app.mail_adapters import MailAdapterError


class DraftPayload(BaseModel):
    draft_id: str | None = None
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str = ""
    html_body: str | None = None
    text_body: str | None = None
    attachment_ids: list[str] = Field(default_factory=list)


def _draft_key(email: str, draft_id: str) -> str:
    return f"draft:{email}:{draft_id}"


def _draft_index_key(email: str) -> str:
    return f"drafts:{email}"


def _draft_message(session: AuthSession, payload: DraftPayload) -> EmailMessage:
    message = EmailMessage()
    message["From"] = session.email
    if payload.draft_id:
        message["X-Draft-ID"] = payload.draft_id
    if payload.to:
        message["To"] = ", ".join(payload.to)
    if payload.cc:
        message["Cc"] = ", ".join(payload.cc)
    if payload.bcc:
        message["Bcc"] = ", ".join(payload.bcc)
    message["Subject"] = payload.subject
    message["X-Webmail-Draft"] = "true"
    if payload.html_body:
        message.set_content(payload.text_body or " ", cte="8bit")
        message.add_alternative(payload.html_body, subtype="html", cte="8bit")
    else:
        message.set_content(payload.text_body or "", cte="8bit")
    for attachment_id in payload.attachment_ids:
        attachment = load_temp_attachment(session, attachment_id)
        maintype, _, subtype = str(attachment["content_type"]).partition("/")
        if not subtype:
            maintype, subtype = "application", "octet-stream"
        message.add_attachment(
            attachment["content"],
            maintype=maintype,
            subtype=subtype,
            filename=str(attachment["filename"]),
        )
    return message


def save_draft(session: AuthSession, payload: DraftPayload) -> dict[str, Any]:
    is_update = payload.draft_id is not None
    draft_id = payload.draft_id or secrets.token_urlsafe(18)
    payload = payload.model_copy(update={"draft_id": draft_id})
    saved_at = datetime.now(timezone.utc).isoformat()
    data = payload.model_dump()
    data.update({"draft_id": draft_id, "status": "saved", "saved_at": saved_at, "owner": session.email})
    redis = redis_client.get_redis_client()
    redis.hset(
        _draft_key(session.email, draft_id),
        mapping={
            "payload": json.dumps(data, ensure_ascii=False),
            "draft_id": draft_id,
            "owner_email": session.email,
            "to_emails": json.dumps(data["to"], ensure_ascii=False),
            "cc_emails": json.dumps(data["cc"], ensure_ascii=False),
            "bcc_emails": json.dumps(data["bcc"], ensure_ascii=False),
            "subject": data["subject"],
            "html_body": data["html_body"] or "",
            "text_body": data["text_body"] or "",
            "attachment_ids": json.dumps(data["attachment_ids"], ensure_ascii=False),
            "status": "saved",
            "saved_at": saved_at,
        },
    )
    redis.sadd(_draft_index_key(session.email), draft_id)

    adapter = mail_adapters.ImapAdapter(_imap_settings(session))
    try:
        adapter.connect().login()
        if is_update:
            adapter.delete_message(".Drafts", payload.draft_id)
        adapter.append_message(".Drafts", _draft_message(session, payload))
    except MailAdapterError as exc:
        raise AppError(
            "DRAFT_SAVE_FAILED",
            "保存草稿失败",
            http_status=status.HTTP_502_BAD_GATEWAY,
            details={"operation": exc.operation},
        ) from exc
    finally:
        adapter.logout()
    return {"draft_id": draft_id, "status": "saved", "saved_at": saved_at}


def get_draft(session: AuthSession, draft_id: str) -> dict[str, Any]:
    raw = redis_client.get_redis_client().hget(_draft_key(session.email, draft_id), "payload")
    if not raw:
        raise AppError("DRAFT_NOT_FOUND", "草稿不存在", http_status=status.HTTP_404_NOT_FOUND)
    return json.loads(raw)


def delete_draft(session: AuthSession, draft_id: str) -> dict[str, Any]:
    redis = redis_client.get_redis_client()
    key = _draft_key(session.email, draft_id)
    if not redis.exists(key):
        raise AppError("DRAFT_NOT_FOUND", "草稿不存在", http_status=status.HTTP_404_NOT_FOUND)
    redis.delete(key)
    redis.srem(_draft_index_key(session.email), draft_id)
    adapter = mail_adapters.ImapAdapter(_imap_settings(session))
    try:
        adapter.connect().login()
        adapter.delete_message(".Drafts", draft_id)
    except MailAdapterError as exc:
        raise AppError(
            "DRAFT_DELETE_FAILED",
            "删除草稿失败",
            http_status=status.HTTP_502_BAD_GATEWAY,
            details={"operation": exc.operation},
        ) from exc
    finally:
        adapter.logout()
    return {"draft_id": draft_id, "deleted": True}
