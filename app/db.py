"""Подключение к базе и фабрика сессий."""

from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """Общий предок всех таблиц."""


@lru_cache
def get_engine() -> AsyncEngine:
    """Движок на всё приложение.

    Создаётся один раз: внутри движка держится пул соединений, и новый движок
    на каждый запрос означал бы новый пул.
    """
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession]:
    """Сессия базы на время одного запроса.

    Откат делает обработчик: он единственный, кто знает, где кончилась работа.
    """
    async with get_session_factory()() as сессия:
        try:
            yield сессия
            await сессия.commit()
        except Exception:
            await сессия.rollback()
            raise