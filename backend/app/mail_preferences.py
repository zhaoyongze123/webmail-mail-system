"""邮箱用户偏好在数据库中的读取与更新逻辑。

这个模块负责把前端偏好设置映射到数据库模型，并在异常时安全回退到
默认值，避免偏好读取影响主流程。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db import get_session_factory
from app.models import MailAccount, MailUserPreference
from app.schemas import (
    DEFAULT_SETTINGS_LANGUAGE,
    DEFAULT_SETTINGS_MARK_READ_ON_OPEN,
    DEFAULT_SETTINGS_PAGE_SIZE,
    DEFAULT_SETTINGS_REPLY_QUOTE_POSITION,
    DEFAULT_SETTINGS_TIMEZONE,
)

logger = logging.getLogger("app.mail_preferences")


def default_user_preferences() -> dict[str, Any]:
    """返回系统默认的用户偏好结构。"""
    return {
        "system": {
            "page_size": DEFAULT_SETTINGS_PAGE_SIZE,
            "mark_read_on_open": DEFAULT_SETTINGS_MARK_READ_ON_OPEN,
            "reply_quote_position": DEFAULT_SETTINGS_REPLY_QUOTE_POSITION,
            "language": DEFAULT_SETTINGS_LANGUAGE,
            "timezone": DEFAULT_SETTINGS_TIMEZONE,
        },
        "user": {
            "display_name": "",
            "profile_title": "",
            "avatar_url": "",
            "bio": "",
        },
        "theme": {
            "mode": "light",
        },
    }


def _preference_model_defaults() -> dict[str, Any]:
    """把默认偏好展开为数据库模型字段默认值。"""
    defaults = default_user_preferences()
    return {
        "page_size": defaults["system"]["page_size"],
        "mark_read_on_open": defaults["system"]["mark_read_on_open"],
        "reply_quote_position": defaults["system"]["reply_quote_position"],
        "language": defaults["system"]["language"],
        "timezone": defaults["system"]["timezone"],
        "display_name": defaults["user"]["display_name"],
        "profile_title": defaults["user"]["profile_title"],
        "avatar_url": defaults["user"]["avatar_url"],
        "bio": defaults["user"]["bio"],
        "theme_mode": defaults["theme"]["mode"],
    }


def _normalize_email(email: str) -> str:
    """标准化邮箱地址，统一大小写和空白。"""
    return email.strip().lower()


def _read_preferences(preferences: MailUserPreference | None) -> dict[str, Any]:
    """把数据库模型转换为前端使用的偏好结构。"""
    if preferences is None:
        return default_user_preferences()
    return {
        "system": {
            "page_size": int(preferences.page_size),
            "mark_read_on_open": bool(preferences.mark_read_on_open),
            "reply_quote_position": preferences.reply_quote_position,
            "language": preferences.language,
            "timezone": preferences.timezone,
        },
        "user": {
            "display_name": preferences.display_name,
            "profile_title": preferences.profile_title,
            "avatar_url": preferences.avatar_url,
            "bio": preferences.bio,
        },
        "theme": {
            "mode": preferences.theme_mode,
        },
    }


def get_user_preferences(email: str) -> dict[str, Any]:
    """读取指定邮箱的偏好，失败时回退默认值。"""
    normalized_email = _normalize_email(email)
    try:
        session_factory = get_session_factory()
        with session_factory() as db_session:
            account = db_session.scalar(select(MailAccount).where(MailAccount.email == normalized_email))
            if account is None:
                return default_user_preferences()
            return _read_preferences(account.preferences)
    except SQLAlchemyError as exc:
        logger.warning("读取用户偏好失败，回退默认值 email=%s error=%s", normalized_email, exc.__class__.__name__)
    except Exception as exc:  # pragma: no cover - 降级保护
        logger.warning("读取用户偏好异常，回退默认值 email=%s error=%s", normalized_email, exc.__class__.__name__)
    return default_user_preferences()


def update_user_preferences(email: str, payload: dict[str, Any]) -> dict[str, Any]:
    """更新指定邮箱的偏好并返回最新值。"""
    normalized_email = _normalize_email(email)
    session_factory = get_session_factory()
    with session_factory() as db_session:
        account = db_session.scalar(select(MailAccount).where(MailAccount.email == normalized_email))
        if account is None:
            return default_user_preferences()
        preferences = account.preferences
        if preferences is None:
            preferences = MailUserPreference(account_id=account.id, **_preference_model_defaults())
            db_session.add(preferences)
            db_session.flush()
        system_payload = payload.get("system") if isinstance(payload.get("system"), dict) else {}
        user_payload = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        theme_payload = payload.get("theme") if isinstance(payload.get("theme"), dict) else {}

        field_mapping = {
            "page_size": system_payload.get("page_size"),
            "mark_read_on_open": system_payload.get("mark_read_on_open"),
            "reply_quote_position": system_payload.get("reply_quote_position"),
            "language": system_payload.get("language"),
            "timezone": system_payload.get("timezone"),
            "display_name": user_payload.get("display_name"),
            "profile_title": user_payload.get("profile_title"),
            "avatar_url": user_payload.get("avatar_url"),
            "bio": user_payload.get("bio"),
            "theme_mode": theme_payload.get("mode"),
        }
        for field, value in field_mapping.items():
            if value is not None:
                setattr(preferences, field, value)
        db_session.commit()
        db_session.refresh(preferences)
        return _read_preferences(preferences)
