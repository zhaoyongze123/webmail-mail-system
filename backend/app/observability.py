"""请求日志、审计日志与运行指标采集。"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from collections import deque
from threading import Lock
from typing import Any

from fastapi import Request
from sqlalchemy import insert

from app import config as app_config
from app.db import get_session_factory
from app.models import AuditLog
from app.responses import get_request_id


access_logger = logging.getLogger("app.access")
audit_logger = logging.getLogger("app.audit")


def _client_ip(request: Request | None) -> str | None:
    """提取请求来源 IP，优先使用转发链路中的真实客户端地址。"""
    if request is None:
        return None
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    if request.client:
        return request.client.host
    return None


class ObservabilityMetrics:
    """进程内观测指标汇总器。

    该对象只负责内存态统计，用于在不依赖外部指标系统的情况下，
    提供请求量、错误量、耗时和审计量的快速视图。
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._started_at = time.monotonic()
        self._request_total = 0
        self._request_error_total = 0
        self._request_duration_ms_total = 0.0
        self._request_duration_ms_max = 0.0
        self._audit_total = 0
        self._requests_by_status = defaultdict(int)
        self._audit_by_event = defaultdict(int)

    def record_request(self, *, status_code: int, duration_ms: float) -> None:
        """记录一次 HTTP 请求的结果。"""
        with self._lock:
            self._request_total += 1
            if status_code >= 400:
                self._request_error_total += 1
            self._request_duration_ms_total += duration_ms
            self._request_duration_ms_max = max(self._request_duration_ms_max, duration_ms)
            self._requests_by_status[str(status_code)] += 1

    def record_audit(self, event_type: str) -> None:
        """记录一次审计事件计数。"""
        with self._lock:
            self._audit_total += 1
            self._audit_by_event[event_type] += 1

    def snapshot(self) -> dict[str, Any]:
        """返回当前指标快照。"""
        with self._lock:
            request_total = self._request_total
            request_duration_avg = self._request_duration_ms_total / request_total if request_total else 0.0
            return {
                "requests_total": request_total,
                "requests_error_total": self._request_error_total,
                "request_duration_ms_avg": round(request_duration_avg, 3),
                "request_duration_ms_max": round(self._request_duration_ms_max, 3),
                "audit_total": self._audit_total,
                "requests_by_status": dict(self._requests_by_status),
                "audit_by_event": dict(self._audit_by_event),
                "uptime_seconds": round(time.monotonic() - self._started_at, 3),
            }

    def reset(self) -> None:
        """重置全部统计数据。"""
        with self._lock:
            self._started_at = time.monotonic()
            self._request_total = 0
            self._request_error_total = 0
            self._request_duration_ms_total = 0.0
            self._request_duration_ms_max = 0.0
            self._audit_total = 0
            self._requests_by_status.clear()
            self._audit_by_event.clear()


metrics_store = ObservabilityMetrics()
_audit_events: deque[dict[str, Any]] = deque(maxlen=200)
_audit_lock = Lock()


def reset_observability_state() -> None:
    """清空内存指标与最近审计事件缓存。"""
    metrics_store.reset()
    with _audit_lock:
        _audit_events.clear()


def get_recent_audit_events() -> list[dict[str, Any]]:
    """返回最近的审计事件列表。"""
    with _audit_lock:
        return list(_audit_events)


def record_request_log(request: Request, status_code: int, duration_ms: float) -> None:
    """写入一次结构化请求日志并同步更新内存指标。"""
    record = {
        "event": "http_request",
        "request_id": get_request_id(request),
        "method": request.method,
        "path": request.url.path,
        "status": status_code,
        "duration": round(duration_ms, 3),
        "duration_ms": round(duration_ms, 3),
    }
    access_logger.info(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    metrics_store.record_request(status_code=status_code, duration_ms=duration_ms)


def record_audit_event(
    request: Request | None,
    event_type: str,
    *,
    success: bool,
    metadata: dict[str, Any] | None = None,
    account_id: Any | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> None:
    """记录审计事件，并在非测试环境持久化到数据库。"""
    event_record = {
        "event_type": event_type,
        "request_id": get_request_id(request) if request is not None else None,
        "success": success,
        "account_id": account_id,
        "metadata": metadata or {},
    }
    with _audit_lock:
        _audit_events.append(event_record)

    request_id = get_request_id(request) if request is not None else None
    audit_logger.info(
        json.dumps(
            {
                "event": "audit",
                "event_type": event_type,
                "request_id": request_id,
                "success": success,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    metrics_store.record_audit(event_type)

    payload = {
        "account_id": account_id,
        "event_type": event_type,
        "request_id": request_id,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "target_type": target_type,
        "target_id": target_id,
        "ip": _client_ip(request),
        "user_agent": request.headers.get("user-agent") if request is not None else None,
        "success": success,
        "metadata": metadata or {},
    }

    settings = app_config.get_settings()
    if getattr(settings, "app_env", "") == "test":
        return

    try:
        session_factory = get_session_factory()
        with session_factory() as session:
            session.execute(insert(AuditLog.__table__).values(**payload))
            session.commit()
    except Exception as exc:  # pragma: no cover - 审计失败不应影响主流程
        audit_logger.warning(
            json.dumps(
                {
                    "event": "audit_write_failed",
                    "event_type": event_type,
                    "request_id": request_id,
                    "success": success,
                    "error": exc.__class__.__name__,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
