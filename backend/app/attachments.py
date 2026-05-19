"""邮件附件临时存储与分块上传相关工具。

这个模块负责把前端上传的附件先写入 Redis，支持普通上传、分块上传、
临时读取和过期回收等流程，供写信和草稿保存链路复用。
"""

from __future__ import annotations

import base64
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import UploadFile, status

from app.auth import AuthSession
from app.config import get_settings
from app.errors import AppError
from app import redis_client
from app.security import validate_attachment_id


def _safe_filename(value: str) -> str:
    """清理附件文件名，去掉路径和危险控制字符。"""
    filename = value.replace("\\", "/").split("/")[-1].strip()
    filename = re.sub(r"[\r\n\x00]+", "", filename)
    return filename or "attachment"


def temp_attachment_key(email: str, attachment_id: str) -> str:
    """生成附件临时缓存的 Redis 键。"""
    return f"attachment:temp:{email}:{attachment_id}"


def temp_attachment_chunk_key(email: str, attachment_id: str) -> str:
    """生成附件分块缓存的 Redis 键。"""
    return f"attachment:temp:chunk:{email}:{attachment_id}"


def persist_temp_attachment(
    session: AuthSession,
    *,
    attachment_id: str,
    filename: str,
    content_type: str,
    content: bytes,
    ttl_seconds: int,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    """把完整附件写入临时缓存，供写信/草稿链路短期复用。"""
    expires_at = expires_at or datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    redis = redis_client.get_redis_client()
    redis.hset(
        temp_attachment_key(session.email, attachment_id),
        mapping={
            "attachment_id": attachment_id,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": str(len(content)),
            "content_b64": base64.b64encode(content).decode("ascii"),
            "content_preview": content.decode("utf-8", errors="ignore"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at.isoformat(),
        },
    )
    redis.expire(temp_attachment_key(session.email, attachment_id), ttl_seconds)
    return {
        "attachment_id": attachment_id,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(content),
        "expires_at": expires_at.isoformat(),
    }


def _load_received_chunks(raw: dict[str, Any]) -> set[int]:
    """解析已收到的分块序号集合。"""
    try:
        return {int(item) for item in json.loads(str(raw.get("received_chunks") or "[]"))}
    except json.JSONDecodeError:
        return set()


async def store_temp_attachment_chunk(
    session: AuthSession,
    *,
    attachment_id: str,
    filename: str,
    content_type: str,
    file_size_bytes: int,
    chunk_index: int,
    total_chunks: int,
    chunk: UploadFile,
) -> dict[str, Any]:
    """接收单个附件分块，必要时拼装成完整附件后落入临时缓存。"""
    validate_attachment_id(attachment_id)
    if chunk_index >= total_chunks:
        raise AppError(
            "ATTACHMENT_CHUNK_INVALID",
            "附件分块序号不合法",
            http_status=status.HTTP_400_BAD_REQUEST,
        )
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
    if file_size_bytes > max_bytes:
        raise AppError(
            "ATTACHMENT_TOO_LARGE",
            f"附件总大小不能超过 {max_label} MB",
            http_status=status.HTTP_413_CONTENT_TOO_LARGE,
        )

    content = await chunk.read()
    safe_filename = _safe_filename(filename)
    redis = redis_client.get_redis_client()
    chunk_key = temp_attachment_chunk_key(session.email, attachment_id)
    raw = redis.hgetall(chunk_key)
    if raw:
        existing_filename = str(raw.get("filename") or safe_filename)
        existing_content_type = str(raw.get("content_type") or content_type)
        existing_size_bytes = int(raw.get("size_bytes") or file_size_bytes)
        existing_total_chunks = int(raw.get("total_chunks") or total_chunks)
        if (
            existing_filename != safe_filename
            or existing_content_type != content_type
            or existing_size_bytes != file_size_bytes
            or existing_total_chunks != total_chunks
        ):
            raise AppError(
                "ATTACHMENT_CHUNK_CONFLICT",
                "附件分块信息不一致",
                http_status=status.HTTP_409_CONFLICT,
            )

    received_chunks = _load_received_chunks(raw)
    received_chunks.add(chunk_index)
    expires_at = raw.get("expires_at") if raw else None
    if not expires_at:
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
    redis.hset(
        chunk_key,
        mapping={
            "attachment_id": attachment_id,
            "filename": safe_filename,
            "content_type": content_type,
            "size_bytes": str(file_size_bytes),
            "total_chunks": str(total_chunks),
            "received_chunks": json.dumps(sorted(received_chunks), ensure_ascii=False),
            "expires_at": str(expires_at),
            f"chunk:{chunk_index}": base64.b64encode(content).decode("ascii"),
            f"chunk_size:{chunk_index}": str(len(content)),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    redis.expire(chunk_key, ttl_seconds)

    complete = len(received_chunks) == total_chunks
    attachment: dict[str, Any] = {
        "attachment_id": attachment_id,
        "filename": safe_filename,
        "content_type": content_type,
        "size_bytes": file_size_bytes,
        "expires_at": str(expires_at),
        "complete": complete,
        "uploaded_chunks": len(received_chunks),
        "total_chunks": total_chunks,
    }
    if not complete:
        return attachment

    assembled = b"".join(
        base64.b64decode(str(redis.hget(chunk_key, f"chunk:{index}") or "").encode("ascii"))
        for index in range(total_chunks)
    )
    if len(assembled) != file_size_bytes:
        redis.delete(chunk_key)
        raise AppError(
            "ATTACHMENT_CHUNK_CONFLICT",
            "附件分块内容不完整",
            http_status=status.HTTP_409_CONFLICT,
        )

    persisted = persist_temp_attachment(
        session,
        attachment_id=attachment_id,
        filename=safe_filename,
        content_type=content_type,
        content=assembled,
        ttl_seconds=ttl_seconds,
        expires_at=datetime.fromisoformat(str(expires_at)),
    )
    redis.delete(chunk_key)
    persisted.update({
        "complete": True,
        "uploaded_chunks": total_chunks,
        "total_chunks": total_chunks,
    })
    return persisted


def load_temp_attachment(session: AuthSession, attachment_id: str) -> dict[str, Any]:
    """从临时缓存读取附件内容。"""
    validate_attachment_id(attachment_id)
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
    """批量上传附件并写入临时缓存。"""
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
    payloads: list[dict[str, Any]] = []
    total_size = 0

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
            persist_temp_attachment(
                session,
                attachment_id=secrets.token_urlsafe(18),
                filename=_safe_filename(file.filename or "attachment"),
                content_type=file.content_type or "application/octet-stream",
                content=content,
                ttl_seconds=ttl_seconds,
            )
        )

    return [
        payload
        for payload in payloads
    ]
