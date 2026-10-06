"""Общие фикстуры тестов.

Тесты идут против настоящего PostgreSQL из docker compose, а не против SQLite:
в моделях есть то, что на SQLite не заработает, и подменять хранилище значило бы
тестировать не то, что пойдёт в бой.

Миграции прогоняются по-настоящему, отдельной командой. Иначе схема в тестах и
схема в бою разошлись бы незаметно.
"""

import asyncio
import os
import subprocess
from collections.abc import AsyncGenerator

import asyncpg
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.config import get_settings

ТЕСТОВАЯ_БАЗА = "booking_test"

# Порядок очистки не важен: внешних ключей между этими таблицами нет, а
# ограничения проверяются на вставке, а не на очистке.
ТАБЛИЦЫ = (
    "bookings",
    "sessions",
    "event_types",
    "schedules",
    "auth_sessions",
    "oauth_tokens",
    "users",
)


@pytest.fixture(scope="session")
def настройки_тестов():
    """Настройки с адресом тестовой базы."""
    настройки = get_settings()
    assert настройки.test_database_url, "Не задан TEST_DATABASE_URL"
    return настройки


@pytest_asyncio.fixture(loop_scope="session")
async def движок(настройки_тестов) -> AsyncEngine:
    """Движок на тестовую базу, схема создана миграциями.

    Цикл событий общий на весь прогон, такой же, как у тестов: соединения
    asyncpg живут дольше одного теста, и при смене цикла они остались бы
    привязаны к ушедшему.
    """
    await _создать_базу_если_нет(настройки_тестов.database_url, ТЕСТОВАЯ_БАЗА)
    await _прогнать_миграции(настройки_тестов.test_database_url)

    движок = create_async_engine(настройки_тестов.test_database_url)
    try:
        yield движок
    finally:
        await движок.dispose()


async def _создать_базу_если_нет(адрес: str, имя: str) -> None:
    """Создать тестовую базу, если её ещё нет."""
    соединение = await asyncpg.connect(адрес.replace("+asyncpg", ""))
    try:
        есть = await соединение.fetchval(
            "select 1 from pg_database where datname = $1", имя
        )
        if not есть:
            await соединение.execute(f'create database "{имя}"')
    finally:
        await соединение.close()


async def _прогнать_миграции(адрес: str) -> None:
    """Накатить миграции на тестовую базу.

    Отдельным процессом, а не вызовом `alembic` из теста: так проверяется
    ровно то, что запускается руками при развёртывании.

    Процесс ждём в отдельном потоке, чтобы не блокировать цикл событий:
    Alembic работает синхронно, и миграции идут дольше одной проверки.
    """
    окружение = {**os.environ, "ALEMBIC_DATABASE_URL": адрес}
    результат = await asyncio.to_thread(
        subprocess.run,
        ["alembic", "upgrade", "head"],
        env=окружение,
        capture_output=True,
        text=True,
        check=False,
    )
    if результат.returncode != 0:
        raise RuntimeError(f"Миграции не накатились:\n{результат.stderr}")


@pytest_asyncio.fixture(loop_scope="session")
async def сессия(движок) -> AsyncGenerator[AsyncSession]:
    """Чистая сессия базы на один тест."""
    фабрика = async_sessionmaker(движок, expire_on_commit=False)
    async with фабрика() as сессия:
        yield сессия
        await сессия.rollback()


@pytest_asyncio.fixture(loop_scope="session", autouse=True)
async def чистая_база(сессия) -> AsyncGenerator[None]:
    """Очистить таблицы до и после теста.

    До теста, а не только после: иначе упавший тест оставил бы данные и
    следующий тест падал бы не по своей причине.
    """
    await _очистить(сессия)
    yield
    await сессия.rollback()
    await _очистить(сессия)


async def _очистить(сессия: AsyncSession) -> None:
    await сессия.execute(
        text("truncate " + ", ".join(ТАБЛИЦЫ) + " restart identity cascade")
    )
    await сессия.commit()


@pytest.fixture
def приложение(сессия):
    """Приложение с подменённой сессией базы.

    Отдельная фикстура, а не всё внутри `клиент_приложения`: тестам, которым
    нужен обмен с Яндексем, надо добраться до того же приложения и подменить
    клиент у него, а не создавать второе.
    """
    from backend.db import get_session
    from backend.main import create_app
    from backend.yandex.client import get_http_client

    собранное = create_app()
    собранное.dependency_overrides[get_session] = lambda: сессия
    # Клиент Яндекса по умолчанию ходит в сеть. Тест, который забыл подменить
    # его, должен упасть на этом, а не молча отвечать в Яндекс.
    собранное.dependency_overrides[get_http_client] = клиент_без_сети
    return собранное


async def клиент_без_сети():
    """Зависимость вместо клиента Яндекса: любое обращение проваливает тест."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(_не_должен_звать)) as клиент:
        yield клиент


async def _не_должен_звать(запрос) -> httpx.Response:
    raise AssertionError(
        f"Тест не подменил клиента Яндекса, а приложение обратилось к {запрос.url}"
    )


@pytest_asyncio.fixture(loop_scope="session")
async def клиент_приложения(
    приложение,
) -> AsyncGenerator[AsyncClient]:
    """HTTP-клиент поверх приложения с подменёнными зависимостями."""
    async with AsyncClient(
        transport=ASGITransport(app=приложение), base_url="http://testserver"
    ) as клиент:
        yield клиент