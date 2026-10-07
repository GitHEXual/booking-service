"""Тесты создания встречи в Телемосте, писем организатору и напоминаний.

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


class TestТекстыПисем:
    def test_гостю_есть_ссылка_и_время(self):
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

    def test_тема_гостю_называет_встречу(self):
        assert "Консультация" in mail.тема_письма("Консультация")

    def test_организатору_есть_гость_почта_и_ссылка(self):
        текст = mail.текст_письма_организатору(
            встреча="Консультация",
            гость="Пётр",
            почта="petr@example.com",
            время="15.10.2026 в 10:00",
            ссылка="https://telemost.yandex.ru/j/1",
        )
        assert "Пётр" in текст
        assert "petr@example.com" in текст
        assert "https://telemost.yandex.ru/j/1" in текст

    def test_тема_о_заявке(self):
        assert "Консультация" in mail.тема_письма_о_заявке("Консультация")


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


async def _сеанс_с_гостем(
    сессия, владелец: User, *, начало: datetime, почта: str = "petr@example.com"
) -> Session:
    """Сеанс с одной действующей заявкой."""
    сеанс = Session(
        event_type_id=1,
        owner_id=владелец.id,
        start_at=начало,
        end_at=начало + timedelta(minutes=30),
    )
    сессия.add(сеанс)
    await сессия.flush()

    сессия.add(
        Booking(
            event_type_id=1,
            owner_id=владелец.id,
            session_id=сеанс.id,
            guest_name="Пётр",
            guest_email=почта,
            guest_email_bidx=слепой_индекс(почта),
            guest_timezone=КРАСНОЯРСК,
            consent_version="2026-10-01",
            consent_at=datetime.now(UTC),
        )
    )
    await сессия.flush()
    return сеанс


@pytest.fixture
async def встреча_скоро(эксперт_с_токеном, сессия) -> Session:
    """Сеанс, который начнётся через пять минут, с одним гостем."""
    return await _сеанс_с_гостем(
        сессия, эксперт_с_токеном, начало=datetime.now(UTC) + timedelta(minutes=5)
    )


def подмена_телемоста() -> dict:
    """Подмена вызова Телемоста: создаём встречу и запоминаем токен."""
    создано: dict = {}

    async def создать(http, *, access_token):
        создано["токен"] = access_token
        return telemost.Встреча("conf-1", "https://telemost.yandex.ru/j/1")

    return создано, создать


class TestПодготовкаВстреч:
    async def test_встреча_создаётся_и_организатору_уходит_письмо(
        self, встреча_скоро, сессия, monkeypatch
    ):
        создано, создать = подмена_телемоста()
        monkeypatch.setattr(telemost, "создать_встречу", создать)

        assert await worker.подготовить_встречи(сессия) == 1

        assert создано["токен"] == "token", "встреча создаётся токеном эксперта"
        assert встреча_скоро.join_url == "https://telemost.yandex.ru/j/1"
        assert встреча_скоро.conference_status == "ready"
        assert встреча_скоро.meeting_password

        # Письмо одно: организатору, со ссылкой и данными гостя. Гость получит
        # своё перед началом встречи.
        письма = (await сессия.execute(select(OutboxEvent))).scalars().all()
        assert len(письма) == 1
        письмо = письма[0]
        assert письмо.kind == worker.ПИСЬМО_О_ЗАЯВКЕ
        assert письмо.to_email == "expert@example.com"
        assert "Пётр" in письмо.body
        assert "petr@example.com" in письмо.body
        assert "https://telemost.yandex.ru/j/1" in письмо.body

    async def test_встреча_создаётся_сразу_а_не_перед_началом(
        self, эксперт_с_токеном, сессия, monkeypatch
    ):
        # Гость записался на встречу через два часа: ссылка нужна организатору
        # уже сейчас, а не за пять минут до начала.
        _, создать = подмена_телемоста()
        monkeypatch.setattr(telemost, "создать_встречу", создать)

        далёкий = await _сеанс_с_гостем(
            сессия,
            эксперт_с_токеном,
            начало=datetime.now(UTC) + timedelta(hours=2),
        )

        assert await worker.подготовить_встречи(сессия) == 1
        assert далёкий.join_url == "https://telemost.yandex.ru/j/1"

    async def test_ссылка_заданная_вручную_не_перетирается(
        self, встреча_скоро, сессия
    ):
        # Пока у приложения нет прав на Телемост, ссылку вписывают руками.
        # Повторный проход не должен её затирать.
        встреча_скоро.join_url = "https://telemost.yandex.ru/j/ручная"
        await сессия.flush()

        assert await worker.подготовить_встречи(сессия) == 0
        assert встреча_скоро.join_url == "https://telemost.yandex.ru/j/ручная"

    async def test_отказ_телемоста_не_роняет_задачу(
        self, встреча_скоро, сессия, monkeypatch
    ):
        async def отказ(http, *, access_token):
            raise telemost.TelemostError("нет доступа")

        monkeypatch.setattr(telemost, "создать_встречу", отказ)

        # Ошибка не должна вылетать: сеанс просто помечается неудачей.
        assert await worker.подготовить_встречи(сессия) == 1
        assert встреча_скоро.conference_status == "failed"
        assert встреча_скоро.join_url is None

        письма = (await сессия.execute(select(OutboxEvent))).scalars().all()
        assert письма == [], "без встречи организатору писать нечего"


class TestНапоминанияГостю:
    async def test_гостю_напоминают_перед_началом(self, встреча_скоро, сессия):
        встреча_скоро.join_url = "https://telemost.yandex.ru/j/1"
        await сессия.flush()

        assert await worker.подготовить_напоминания(сессия, настройки()) == 1

        письма = (await сессия.execute(select(OutboxEvent))).scalars().all()
        assert len(письма) == 1
        assert письма[0].kind == worker.ПИСЬМО_О_НАЧАЛЕ
        assert письма[0].to_email == "petr@example.com"
        assert "https://telemost.yandex.ru/j/1" in письма[0].body

    async def test_напоминание_не_ставится_дважды(self, встреча_скоро, сессия):
        встреча_скоро.join_url = "https://telemost.yandex.ru/j/1"
        await сессия.flush()

        assert await worker.подготовить_напоминания(сессия, настройки()) == 1
        assert await worker.подготовить_напоминания(сессия, настройки()) == 0

        письма = (await сессия.execute(select(OutboxEvent))).scalars().all()
        assert len(письма) == 1, "гостю не должно уйти два одинаковых письма"

    async def test_далёкой_встрече_не_напоминают(
        self, эксперт_с_токеном, сессия
    ):
        далёкий = await _сеанс_с_гостем(
            сессия,
            эксперт_с_токеном,
            начало=datetime.now(UTC) + timedelta(hours=2),
        )
        далёкий.join_url = "https://telemost.yandex.ru/j/1"
        await сессия.flush()

        assert await worker.подготовить_напоминания(сессия, настройки()) == 0

    async def test_окно_сдвигается_настройкой(
        self, встреча_скоро, сессия
    ):
        """Момент напоминания задаётся настройкой, а не константой в коде."""
        встреча_скоро.start_at = datetime.now(UTC) + timedelta(minutes=25)
        встреча_скоро.join_url = "https://telemost.yandex.ru/j/1"
        await сессия.flush()

        # В обычном окне встреча через 25 минут не попадает.
        assert await worker.подготовить_напоминания(сессия, настройки()) == 0

        широкие = настройки(
            {"reminder_lead_minutes": 30, "reminder_window_minutes": 10}
        )
        assert await worker.подготовить_напоминания(сессия, широкие) == 1


class TestРассылка:
    async def test_письмо_отправляется_и_помечается(
        self, встреча_скоро, сессия, monkeypatch
    ):
        отправлено = []

        def отправить(настройки, *, кому, тема, текст):
            отправлено.append((кому, тема))

        monkeypatch.setattr(mail, "отправить", отправить)

        заявка = (
            await сессия.execute(
                select(Booking).where(Booking.session_id == встреча_скоро.id)
            )
        ).scalar_one()

        сессия.add(
            OutboxEvent(
                kind=worker.ПИСЬМО_О_НАЧАЛЕ,
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
            await сессия.execute(
                select(Booking).where(Booking.session_id == встреча_скоро.id)
            )
        ).scalar_one()
        сессия.add(
            OutboxEvent(
                kind=worker.ПИСЬМО_О_НАЧАЛЕ,
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


def настройки(запас: dict | None = None):
    """Настройки для тестов: почту воркер не трогает, она подменена."""
    from backend.config import get_settings

    if not запас:
        return get_settings()
    return get_settings().model_copy(update=запас)
