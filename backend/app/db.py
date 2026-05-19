from __future__ import annotations

"""数据库引擎、会话工厂与元数据命名约定。"""

from functools import lru_cache

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """所有 ORM 模型共享的 Declarative 基类。"""
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def get_database_url() -> str:
    """读取当前进程应使用的数据库连接串。"""
    return get_settings().database_url


@lru_cache(maxsize=1)
def get_engine(database_url: str | None = None):
    """创建或复用 SQLAlchemy 引擎实例。"""
    return create_engine(
        database_url or get_database_url(),
        pool_pre_ping=True,
    )


def get_session_factory(database_url: str | None = None) -> sessionmaker[object]:
    """创建配置好的 SQLAlchemy 会话工厂。"""
    return sessionmaker(
        bind=get_engine(database_url),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
