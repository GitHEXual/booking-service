"""Тесты создания встречи в Телемосте и фоновой задачи.

Сеть не используется: транспорт httpx подменён, поэтому проверяется ровно то,
что уходит в Телемост, и то, как на это реагирует приложение.
"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from backend import mail, telemost, worker
from backend.crypto import слепой_индекс
from backend.models import Booking, EventType, OAuthToken, OutboxEvent, Schedule, Session, User

КРАСНОЯРСК = "Asia/Krasnoyarsk"


def клиент(обработчик) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(обработчик))


class TestСозданиеВстречи:
    async def test_уходит_правильный_запрос(self):
        видел = {}

        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            видел["адрес"] = str(запрос.url)
            видел["метод"] = запрос.method
            видел["токен"] = запрос.headers.get("authorization")
            видел["тело"] = json.loads(запрос.read())
            return httpx.Response(
                201, json={"id": "conf-1", "join_url": "https://telemost.yandex.ru/j/1"}
            )

        async with клиент(обработчик) as http:
            встреча = await telemost.создать_встречу(
                http, access_token="token-expert"
            )

        assert видел["метод"] == "POST"
        assert видел["адрес"] == telemost.СОЗДАТЬ_ВСТРЕЧУ
        # Имя схемы именно `OAuth`, в документации Яндекса оно такое.
        assert видел["токен"] == "OAuth token-expert"
        assert видел["тело"] == {"waiting_room_level": "PUBLIC"}
        assert встреча.conference_id == "conf-1"
        assert встреча.join_url == "https://telemost.yandex.ru/j/1"

    async def test_без_созданной_встречи_ошибка(self):
        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json={})

        async with клиент(обработчик) as http:
            with pytest.raises(telemost.TelemostError):
                await telemost.создать_встречу(http, access_token="token")

    async def test_нет_доступа_объясняется_по_человечески(self):
        # Человек должен понять, что виновато приложение, а не его аккаунт.
        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "Forbidden"})

        async with клиент(обработчик) as http:
            with pytest.raises(telemost.TelemostError) as ошибка:
                await telemost.создать_встречу(http, access_token="token")

        assert telemost.НУЖНОЕ_ПРАВО in str(ошибка.value)
        assert "Forbidden" not in str(ошибка.value)

    async def test_сеть_доступна_но_упала(self):
        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("сеть недоступна")

        async with клиент(обработчик) as http:
            with pytest.raises(telemost.TelemostError):
                await telemost.создать_встречу(http, access_token="token")


class TestТекстПисьма:
    def test_в_письме_есть_ссылка_и_время(self):
        текст = mail.текст_письма(
            имя="Пётр",
            встреча="Консультация",
            эксперт="Максим",
            время="15.10.2026 в 10:00",
            ссылка="https://telemost.yandex.ru/j/1",
        )
        assert "https://telemost.yandex.ru/j/1" in текст
        assert "15.10.2026 в 10:00" in текст
        assert "Пётр" in текст

    def test_тема_называет_встречу(self):
        assert "Консультация" in mail.тема_письма("Консультация")


@pytest.fixture
async def эксперт_с_токеном(сессия) -> User:
    """Эксперт с расписанием, встречей и действующим токеном Яндекса."""
    человек = User(
        yandex_id="1",
        login="shkutanmaxaon",
        email="expert@example.com",
        display_name="Максим",
        timezone=КРАСНОЯРСК,
    )
    сессия.add(человек)
    await сессия.flush()

    расписание = Schedule(
        owner_id=человек.id, weekdays="1,2,3,4,5", start_time="09:00", end_time="18:00"
    )
    сессия.add(расписание)
    await сессия.flush()

    вид = EventType(
        owner_id=человек.id,
        schedule_id=расписание.id,
        name="Консультация",
        slug="konsultaciya",
        duration_minutes=30,
    )
    сессия.add(вид)
    await сессия.flush()

    сессия.add(
        OAuthToken(
            user_id=человек.id,
            access_token="token",
            refresh_token="старый",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await сессия.flush()
    return человек


@pytest.fixture
async def встреча_скоро(эксперт_с_токеном, сессия) -> Session:
    """Сеанс, который начнётся через пять минут, с одним гостем."""
    начало = datetime.now(UTC) + timedelta(minutes=5)
    сеанс = Session(
        event_type_id=1,
        owner_id=эксперт_с_токеном.id,
        start_at=начало,
        end_at=начало + timedelta(minutes=30),
    )
    сессия.add(сеанс)
    await сессия.flush()

    заявка = Booking(
        event_type_id=1,
        owner_id=эксперт_с_токеном.id,
        session_id=сеанс.id,
        guest_name="Пётр",
        guest_email="petr@example.com",
        guest_email_bidx=слепой_индекс("petr@example.com"),
        guest_timezone=КРАСНОЯРСК,
        consent_version="2026-10-01",
        consent_at=datetime.now(UTC),
    )
    сессия.add(заявка)
    await сессия.flush()
    return сеанс


def подмена_телемоста(создана: dict):
    """Подмена вызова Телемоста: создаём встречу и запоминаем токен."""
    видел = {}

    async def создать(http, *, access_token):
        видел["токен"] = access_token
        встреча = telemost.Встреча("conf-1", "https://telemost.yandex.ru/j/1")
        создана.update(встреча=встреча, токен=access_token)
        return встреча

    return видел, создать


class TestПодготовкаВстреч:
    async def test_встреча_создаётся_и_письмо_кладётся(
        self, встреча_скоро, сессия, monkeypatch
    ):
        создано = {}
        видел, создать = подмена_телемоста(создано)
        monkeypatch.setattr(telemost, "создать_встречу", создать)

        # Создаём встречу без похода в сеть: подменяем httpx-клиент воркера.
        настоящий = httpx.AsyncClient

        def пустой_клиент(*args, **kwargs):
            return настоящий(
                transport=httpx.MockTransport(
                    lambda запрос: httpx.Response(201, json={})
                )
            )

        monkeypatch.setattr(worker.httpx, "AsyncClient", пустой_клиент)

        assert await worker.подготовить_встречи(сессия, настройки()) == 1

        assert создано["токен"] == "token", "встреча создаётся токеном эксперта"
        assert встреча_скоро.join_url == "https://telemost.yandex.ru/j/1"
        assert встреча_скоро.conference_status == "ready"
        assert встреча_скоро.meeting_password

        # Письма два: гостю со ссылкой и организатору, он ведёт встречу.
        письма = (await сессия.execute(select(OutboxEvent))).scalars().all()
        assert len(письма) == 2
        все_к = "".join(письмо.body for письмо in письма)
        assert "https://telemost.yandex.ru/j/1" in все_к

        гостю = [п for п in письма if п.to_email == "petr@example.com"]
        эксперту = [п for п in письма if п.to_email == "expert@example.com"]
        assert len(гостю) == 1, "гостю должно уйти письмо"
        assert len(эксперту) == 1, "организатору тоже нужна ссылка"
        assert "Пётр" in гостю[0].body

    async def test_ссылка_заданная_вручную_не_перетирается(
        self, встреча_скоро, сессия
    ):
        # Пока у приложения нет прав на Телемост, ссылку вписывают руками.
        # Повторный проход не должен её затирать.
        встреча_скоро.join_url = "https://telemost.yandex.ru/j/ручная"
        await сессия.flush()

        assert await worker.подготовить_встречи(сессия, настройки()) == 0
        assert встреча_скоро.join_url == "https://telemost.yandex.ru/j/ручная"

    async def test_встреча_далеко_не_трогается(self, сессия, эксперт_с_токеном):
        # До пяти минут встречи трогать рано: ссылка ещё может измениться.
        далёкий = Session(
            event_type_id=1,
            owner_id=эксперт_с_токеном.id,
            start_at=datetime.now(UTC) + timedelta(hours=2),
            end_at=datetime.now(UTC) + timedelta(hours=2, minutes=30),
        )
        сессия.add(далёкий)
        await сессия.flush()

        assert await worker.подготовить_встречи(сессия, настройки()) == 0
        assert далёкий.join_url is None

    async def test_отказ_телемоста_не_роняет_задачу(
        self, встреча_скоро, сессия, monkeypatch
    ):
        async def отказ(http, *, access_token):
            raise telemost.TelemostError("нет доступа")

        monkeypatch.setattr(telemost, "создать_встречу", отказ)

        настоящий = httpx.AsyncClient

        def пустой_клиент(*args, **kwargs):
            return настоящий(
                transport=httpx.MockTransport(
                    lambda запрос: httpx.Response(201, json={})
                )
            )

        monkeypatch.setattr(worker.httpx, "AsyncClient", пустой_клиент)

        # Ошибка не должна вылетать: сеанс просто помечается неудачей.
        assert await worker.подготовить_встречи(сессия, настройки()) == 1
        assert встреча_скоро.conference_status == "failed"
        assert встреча_скоро.join_url is None


class TestРассылка:
    async def test_письмо_отправляется_и_помечается(
        self, встреча_скоро, сессия, monkeypatch
    ):
        отправлено = []

        def отправить(настройки, *, кому, тема, текст):
            отправлено.append((кому, тема))

        monkeypatch.setattr(mail, "отправить", отправить)

        заявка = (
            await сессия.execute(select(Booking).where(Booking.session_id == встреча_скоро.id))
        ).scalar_one()

        сессия.add(
            OutboxEvent(
                kind="meeting_soon",
                booking_id=заявка.id,
                to_email="petr@example.com",
                subject="Встреча начинается",
                body="ссылка",
            )
        )
        await сессия.flush()

        assert await worker.разослать(сессия, настройки()) == 1
        assert отправлено == [("petr@example.com", "Встреча начинается")]

        письма = (await сессия.execute(select(OutboxEvent))).scalars().all()
        assert письма[0].sent_at is not None

    async def test_ошибка_почты_оставляет_письмо_в_очереди(
        self, встреча_скоро, сессия, monkeypatch
    ):
        def отказать(настройки, *, кому, тема, текст):
            raise OSError("ящик недоступен")

        monkeypatch.setattr(mail, "отправить", отказать)

        заявка = (
            await сессия.execute(select(Booking).where(Booking.session_id == встреча_скоро.id))
        ).scalar_one()
        сессия.add(
            OutboxEvent(
                kind="meeting_soon",
                booking_id=заявка.id,
                to_email="petr@example.com",
                subject="Встреча",
                body="ссылка",
            )
        )
        await сессия.flush()

        assert await worker.разослать(сессия, настройки()) == 0

        письма = (await сессия.execute(select(OutboxEvent))).scalars().all()
        # Письмо не пропало: его попробует отправить следующий проход.
        assert письма[0].sent_at is None
        assert письма[0].attempts == 1
        assert "недоступен" in письма[0].last_error


def настройки():
    """Настройки для тестов: почту воркер не трогает, она подменена."""
    from backend.config import get_settings

    return get_settings()
