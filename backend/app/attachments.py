from __future__ import annotations

import base64
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import UploadFile, status

from app.auth import AuthSession
from app.config import get_settings
from app.errors import AppError
from app import redis_client


def _safe_filename(value: str) -> str:
    filename = value.replace("\\", "/").split("/")[-1].strip()
    filename = re.sub(r"[\r\n\x00]+", "", filename)
    return filename or "attachment"


def temp_attachment_key(email: str, attachment_id: str) -> str:
    return f"attachment:temp:{email}:{attachment_id}"


def load_temp_attachment(session: AuthSession, attachment_id: str) -> dict[str, Any]:
    data = redis_client.get_redis_client().hgetall(temp_attachment_key(session.email, attachment_id))
    if not data:
        data = redis_client.get_redis_client().hgetall(f"compose_upload:{attachment_id}")
    if not data:
        raise AppError("ATTACHMENT_NOT_FOUND", "附件不存在或已过期", http_status=status.HTTP_404_NOT_FOUND)
    return {
        "attachment_id": attachment_id,
        "filename": str(data["filename"]),
        "content_type": str(data["content_type"]),
        "size_bytes": int(data["size_bytes"]),
        "content": base64.b64decode(str(data["content_b64"]).encode("ascii")),
    }


async def upload_temp_attachments(session: AuthSession, files: list[UploadFile]) -> list[dict[str, Any]]:
    settings = get_settings()
    max_mb = int(getattr(settings, "attachment_max_mb", 0) or 0)
    max_bytes = int(
        getattr(settings, "attachment_max_size_bytes", 0)
        or getattr(settings, "attachment_upload_max_size_bytes", 0)
        or getattr(settings, "max_attachment_upload_size_bytes", 0)
        or getattr(settings, "attachments_max_size_bytes", 0)
        or getattr(settings, "attachment_max_total_size_bytes", 0)
        or max_mb * 1024 * 1024
    )
    ttl_seconds = int(
        getattr(settings, "attachment_ttl_seconds", 0)
        or getattr(settings, "attachment_upload_ttl_seconds", 0)
        or getattr(settings, "attachment_temp_ttl_seconds", 0)
        or getattr(settings, "attachments_ttl_seconds", 0)
        or 3600
    )
    max_label = max_mb or max_bytes // 1024 // 1024
    redis = redis_client.get_redis_client()
    payloads: list[dict[str, Any]] = []
    total_size = 0
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

    for file in files:
        content = await file.read()
        total_size += len(content)
        if total_size > max_bytes:
            raise AppError(
                "ATTACHMENT_TOO_LARGE",
                f"附件总大小不能超过 {max_label} MB",
                http_status=status.HTTP_413_CONTENT_TOO_LARGE,
            )
        payloads.append(
            {
                "attachment_id": secrets.token_urlsafe(18),
                "filename": _safe_filename(file.filename or "attachment"),
                "content_type": file.content_type or "application/octet-stream",
                "size_bytes": len(content),
                "content": content,
                "expires_at": expires_at.isoformat(),
            }
        )

    for payload in payloads:
        redis.hset(
            temp_attachment_key(session.email, payload["attachment_id"]),
            mapping={
                "attachment_id": payload["attachment_id"],
                "filename": payload["filename"],
                "content_type": payload["content_type"],
                "size_bytes": str(payload["size_bytes"]),
                "content_b64": base64.b64encode(payload["content"]).decode("ascii"),
                "content_preview": payload["content"].decode("utf-8", errors="ignore"),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": payload["expires_at"],
            },
        )
        redis.expire(temp_attachment_key(session.email, payload["attachment_id"]), ttl_seconds)

    return [
        {
            "attachment_id": payload["attachment_id"],
            "filename": payload["filename"],
            "content_type": payload["content_type"],
            "size_bytes": payload["size_bytes"],
            "expires_at": payload["expires_at"],
        }
        for payload in payloads
    ]
