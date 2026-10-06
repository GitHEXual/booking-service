"""Подключение Alembic к настройкам приложения.

Строка подключения и ключи берутся из настроек, а не из `alembic.ini`: иначе
одно и то же пришлось бы держать в двух местах, и миграции поехали бы не туда.
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app import models  # noqa: F401  импорт нужен, чтобы модели попали в metadata
from app.config import get_settings
from app.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def строка_подключения() -> str:
    """К какой базе подключаться.

    `ALEMBIC_DATABASE_URL` нужен, чтобы прогнать миграции на тестовой базе:
    в обычном окружении такой переменной нет.
    """
    return os.environ.get("ALEMBIC_DATABASE_URL") or get_settings().database_url


def прогнать_миграции(connection: Connection) -> None:
    """Настроить контекст на готовом соединении и выполнить миграции.

    Отдельная синхронная функция: Alembic работает с синхронным соединением,
    а мы держим асинхронный движок.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def миграции_офлайн() -> None:
    """Генерация SQL без подключения к базе."""
    context.configure(
        url=строка_подключения(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def миграции_онлайн() -> None:
    """Выполнить миграции против живой базы."""
    движок = create_async_engine(строка_подключения())
    try:
        async with движок.connect() as соединение:
            await соединение.run_sync(прогнать_миграции)
    finally:
        await движок.dispose()


if context.is_offline_mode():
    миграции_офлайн()
else:
    asyncio.run(миграции_онлайн())