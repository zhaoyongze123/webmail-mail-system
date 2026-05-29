"""新邮件系统通知与 Web Push 链路。"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from typing import Any

from fastapi import Request, status
from sqlalchemy import select

from app.auth import AuthSession
from app.config import Settings, get_settings
from app.crypto import decrypt_text, encrypt_text
from app.db import get_session_factory
from app.errors import AppError
from app.mail_adapters import ImapAdapter, ImapSettings, MailAdapterError
from app.mail_state import ensure_mail_account
from app.models import MailAccount, MailNotificationCursor, MailNotificationPreference, MailPushSubscription


logger = logging.getLogger("app.notifications")

_worker_lock = threading.Lock()
_worker_thread: threading.Thread | None = None
_worker_stop_event = threading.Event()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _normalize_datetime_from_millis(value: int | float | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    except (OverflowError, TypeError, ValueError):
        return None


def _imap_settings(account: MailAccount, password: str, settings: Settings) -> ImapSettings:
    return ImapSettings(
        host=account.imap_host or settings.mail_imap_host,
        port=account.imap_port or settings.mail_imap_port,
        username=account.email,
        password=password,
        use_ssl=account.imap_ssl,
        starttls=settings.mail_imap_starttls,
        timeout=settings.mail_notification_imap_timeout_seconds,
    )


def _endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def _serialize_subscription(subscription: MailPushSubscription | None) -> dict[str, object] | None:
    if subscription is None:
        return None
    return {
        "endpoint": subscription.endpoint,
        "created_at": subscription.created_at.isoformat() if subscription.created_at else None,
        "updated_at": subscription.updated_at.isoformat() if subscription.updated_at else None,
    }


def _build_notification_payload(message: dict[str, Any]) -> dict[str, Any]:
    sender = message.get("sender") or "新邮件"
    subject = message.get("subject") or "无主题"
    return {
        "title": "新邮件到达",
        "body": f"{sender}: {subject}",
        "tag": f"mail-{message.get('folder', 'INBOX')}-{message.get('uid', '0')}",
        "folder": message.get("folder"),
        "uid": message.get("uid"),
        "message_id": message.get("message_id"),
        "subject": subject,
        "sender": sender,
        "url": f"/?folder={message.get('folder', 'INBOX')}&uid={message.get('uid', '')}&messageId={message.get('message_id', '')}",
    }


def _send_web_push(subscription: MailPushSubscription, payload: dict[str, Any], settings: Settings) -> bool:
    if not settings.web_push_ready:
        return False
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning("Web Push 依赖 pywebpush 未安装，跳过推送 endpoint_hash=%s", subscription.endpoint_hash)
        return False

    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {
                    "p256dh": subscription.p256dh,
                    "auth": subscription.auth,
                },
            },
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=settings.web_push_vapid_private_key,
            vapid_claims={"sub": settings.web_push_vapid_claims_subject},
        )
        return True
    except WebPushException as exc:
        logger.warning("Web Push 发送失败 endpoint_hash=%s error=%s", subscription.endpoint_hash, exc)
        return False


def _get_or_create_account(email: str) -> MailAccount:
    normalized_email = email.strip().lower()
    ensure_mail_account(normalized_email)
    session_factory = get_session_factory()
    with session_factory() as db_session:
        account = db_session.scalar(select(MailAccount).where(MailAccount.email == normalized_email))
        if account is None:
            raise AppError("NOTIFICATION_ACCOUNT_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
        db_session.expunge(account)
        return account


def _get_notification_preference(db_session: Any, account_id: Any, *, create: bool = False) -> MailNotificationPreference | None:
    preference = db_session.scalar(
        select(MailNotificationPreference).where(MailNotificationPreference.account_id == account_id)
    )
    if preference is None and create:
        preference = MailNotificationPreference(account_id=account_id)
        db_session.add(preference)
        db_session.flush()
    return preference


def get_push_subscription_status(session: AuthSession) -> dict[str, object]:
    settings = get_settings()
    account = _get_or_create_account(session.email)
    session_factory = get_session_factory()
    with session_factory() as db_session:
        subscription = db_session.scalar(
            select(MailPushSubscription)
            .where(MailPushSubscription.account_id == account.id)
            .order_by(MailPushSubscription.updated_at.desc(), MailPushSubscription.created_at.desc())
        )
        return {
            "vapid_public_key": settings.web_push_vapid_public_key if settings.web_push_ready else None,
            "subscription": _serialize_subscription(subscription),
        }


def get_notification_status(session: AuthSession) -> dict[str, object]:
    account = _get_or_create_account(session.email)
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as db_session:
        preference = _get_notification_preference(db_session, account.id, create=False)
        subscription = db_session.scalar(
            select(MailPushSubscription)
            .where(MailPushSubscription.account_id == account.id)
            .order_by(MailPushSubscription.updated_at.desc(), MailPushSubscription.created_at.desc())
        )
        return {
            "enabled": bool(preference.enabled) if preference else False,
            "permission_state": preference.permission_state if preference else "default",
            "last_error": preference.last_error if preference else None,
            "has_subscription": subscription is not None,
            "vapid_public_key": settings.web_push_vapid_public_key if settings.web_push_ready else None,
        }


def save_push_subscription_record(session: AuthSession, payload: dict[str, Any], request: Request | None = None) -> dict[str, object]:
    account = _get_or_create_account(session.email)
    session_factory = get_session_factory()
    endpoint = str(payload.get("endpoint") or "").strip()
    keys = payload.get("keys") or {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        raise AppError("NOTIFICATION_SUBSCRIPTION_INVALID", "推送订阅信息不完整", http_status=status.HTTP_400_BAD_REQUEST)

    with session_factory() as db_session:
        subscription = db_session.scalar(
            select(MailPushSubscription).where(
                MailPushSubscription.account_id == account.id,
                MailPushSubscription.endpoint == endpoint,
            )
        )
        if subscription is None:
            subscription = MailPushSubscription(
                account_id=account.id,
                endpoint=endpoint,
                endpoint_hash=_endpoint_hash(endpoint),
                p256dh=p256dh,
                auth=auth,
            )
            db_session.add(subscription)
        subscription.endpoint_hash = _endpoint_hash(endpoint)
        subscription.p256dh = p256dh
        subscription.auth = auth
        subscription.expiration_time = _normalize_datetime_from_millis(payload.get("expiration_time"))
        subscription.user_agent = request.headers.get("user-agent") if request is not None else None
        subscription.last_seen_at = _now()

        preference = _get_notification_preference(db_session, account.id, create=True)
        preference.enabled = True
        preference.permission_state = "granted"
        preference.mailbox_secret_encrypted = encrypt_text(session.password)
        preference.last_error = None
        db_session.commit()
        db_session.refresh(subscription)
        return {"subscription": _serialize_subscription(subscription)}


def delete_push_subscription_record(session: AuthSession) -> dict[str, object]:
    account = _get_or_create_account(session.email)
    session_factory = get_session_factory()
    with session_factory() as db_session:
        subscriptions = db_session.scalars(
            select(MailPushSubscription).where(MailPushSubscription.account_id == account.id)
        ).all()
        for subscription in subscriptions:
            db_session.delete(subscription)
        preference = _get_notification_preference(db_session, account.id, create=False)
        if preference is not None:
            preference.enabled = False
            preference.permission_state = "default"
        db_session.commit()
    return {"deleted": True}


def update_notification_preferences(session: AuthSession, payload: dict[str, Any]) -> dict[str, object]:
    account = _get_or_create_account(session.email)
    session_factory = get_session_factory()
    enabled = bool(payload.get("enabled"))
    permission_state = str(payload.get("permission_state") or ("granted" if enabled else "default"))
    with session_factory() as db_session:
        preference = _get_notification_preference(db_session, account.id, create=True)
        preference.enabled = enabled
        preference.permission_state = permission_state
        preference.mailbox_secret_encrypted = encrypt_text(session.password) if enabled else preference.mailbox_secret_encrypted
        if not enabled:
            preference.last_error = None
        db_session.commit()
        return {
            "enabled": preference.enabled,
            "permission_state": preference.permission_state,
            "last_error": preference.last_error,
        }


def sync_notification_mailbox_secret(email: str, password: str) -> None:
    """在账号改密后同步刷新通知轮询凭据。"""
    account = _get_or_create_account(email)
    session_factory = get_session_factory()
    with session_factory() as db_session:
        preference = _get_notification_preference(db_session, account.id, create=False)
        if preference is None or not preference.enabled:
            return
        preference.mailbox_secret_encrypted = encrypt_text(password)
        preference.last_error = None
        db_session.commit()


def _extract_message_summary(folder_name: str, uid: str, raw_headers: bytes) -> dict[str, Any]:
    message = BytesParser(policy=policy.default).parsebytes(raw_headers)
    return {
        "folder": folder_name,
        "uid": uid,
        "message_id": _decode_header_value(message.get("message-id")),
        "subject": _decode_header_value(message.get("subject")) or "无主题",
        "sender": _decode_header_value(message.get("from")) or "新邮件",
    }


def _poll_account(account_id: Any, settings: Settings) -> None:
    session_factory = get_session_factory()
    with session_factory() as db_session:
        account = db_session.get(MailAccount, account_id)
        if account is None:
            return
        preference = _get_notification_preference(db_session, account.id, create=False)
        if preference is None or not preference.enabled or not preference.mailbox_secret_encrypted:
            return
        subscriptions = db_session.scalars(
            select(MailPushSubscription).where(MailPushSubscription.account_id == account.id)
        ).all()
        if not subscriptions:
            return
        cursor = db_session.scalar(
            select(MailNotificationCursor).where(
                MailNotificationCursor.account_id == account.id,
                MailNotificationCursor.folder_name == "INBOX",
            )
        )
        if cursor is None:
            cursor = MailNotificationCursor(account_id=account.id, folder_name="INBOX")
            db_session.add(cursor)
            db_session.flush()

        password = decrypt_text(preference.mailbox_secret_encrypted)
        adapter = ImapAdapter(_imap_settings(account, password, settings))
        try:
            adapter.connect().login()
            adapter.select_folder("INBOX")
            uids = [int(uid) for uid in adapter.uid_search("ALL") if str(uid).isdigit()]
            if not uids:
                cursor.last_checked_at = _now()
                preference.last_error = None
                db_session.commit()
                return

            max_uid = max(uids)
            if cursor.last_uid is None:
                cursor.last_uid = max_uid
                cursor.last_checked_at = _now()
                preference.last_error = None
                db_session.commit()
                return

            new_uids = [uid for uid in uids if uid > int(cursor.last_uid)][-settings.mail_notification_batch_size:]
            for uid in new_uids:
                headers = adapter.uid_fetch_headers(str(uid))
                payload = _build_notification_payload(_extract_message_summary("INBOX", str(uid), headers))
                successful_push = False
                for subscription in subscriptions:
                    if _send_web_push(subscription, payload, settings):
                        successful_push = True
                if successful_push:
                    cursor.last_uid = uid
                    cursor.last_message_id = str(payload.get("message_id") or "")
            cursor.last_uid = max_uid
            cursor.last_checked_at = _now()
            preference.last_error = None
            db_session.commit()
        except (MailAdapterError, AppError, Exception) as exc:
            preference.last_error = str(exc)
            cursor.last_checked_at = _now()
            db_session.commit()
            logger.warning("新邮件通知轮询失败 account=%s error=%s", account.email, exc)
        finally:
            try:
                adapter.logout()
            except Exception:
                pass


def _poll_loop() -> None:
    settings = get_settings()
    while not _worker_stop_event.is_set():
        try:
            session_factory = get_session_factory()
            with session_factory() as db_session:
                account_ids = db_session.scalars(
                    select(MailNotificationPreference.account_id).where(MailNotificationPreference.enabled.is_(True))
                ).all()
            for account_id in account_ids:
                if _worker_stop_event.is_set():
                    break
                _poll_account(account_id, settings)
        except Exception as exc:
            logger.warning("通知轮询主循环异常 error=%s", exc)
        _worker_stop_event.wait(max(5, settings.mail_notification_poll_interval_seconds))


def start_notification_worker() -> None:
    global _worker_thread
    settings = get_settings()
    if settings.app_env == "test":
        return
    if not settings.mail_notification_poll_enabled:
        logger.info("通知轮询未启用，跳过启动后台线程")
        return
    with _worker_lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _worker_stop_event.clear()
        _worker_thread = threading.Thread(target=_poll_loop, name="mail-notification-worker", daemon=True)
        _worker_thread.start()


def stop_notification_worker() -> None:
    global _worker_thread
    with _worker_lock:
        _worker_stop_event.set()
        if _worker_thread and _worker_thread.is_alive():
            _worker_thread.join(timeout=3)
        _worker_thread = None
