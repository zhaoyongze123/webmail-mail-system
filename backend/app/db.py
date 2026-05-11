from __future__ import annotations

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
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def get_database_url() -> str:
    return get_settings().database_url


@lru_cache(maxsize=1)
def get_engine(database_url: str | None = None):
    return create_engine(
        database_url or get_database_url(),
        pool_pre_ping=True,
    )


def get_session_factory(database_url: str | None = None) -> sessionmaker[object]:
    return sessionmaker(
        bind=get_engine(database_url),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
