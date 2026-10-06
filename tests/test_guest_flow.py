"""Тесты пути гостя: ссылка, сетка слотов и заявка.

Сценарий проверяется целиком, как его проходит человек: открыл ссылку, увидел
время, отправил заявку. Сеть не используется, обмен с Яндексом подменён.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from backend.crypto import слепой_индекс
from backend.models import Booking, EventType, Schedule, User

КРАСНОЯРСК = ZoneInfo("Asia/Krasnoyarsk")
ЛОГИН = "shkutanmaxaon"
SLUG = "konsultaciya"


@pytest.fixture
async def эксперт(сессия) -> User:
    """Эксперт с расписанием и одним видом встречи."""
    человек = User(
        yandex_id="1000034427",
        login=ЛОГИН,
        email="ivan@example.com",
        display_name="Иван",
        timezone="Asia/Krasnoyarsk",
    )
    сессия.add(человек)
    await сессия.flush()

    расписание = Schedule(
        owner_id=человек.id,
        weekdays="1,2,3,4,5",
        start_time="09:00",
        end_time="12:00",
    )
    сессия.add(расписание)
    await сессия.flush()

    сессия.add(
        EventType(
            owner_id=человек.id,
            schedule_id=расписание.id,
            name="Консультация по проекту",
            slug=SLUG,
            duration_minutes=30,
            time_increment_minutes=30,
            min_notice_hours=0,
            horizon_days=30,
        )
    )
    await сессия.flush()
    return человек


async def будущий_слот(клиент) -> str:
    """Первый свободный слот из сетки, в UTC."""
    ответ = (await клиент.get(f"/api/{ЛОГИН}/{SLUG}/slots?дней=7")).json()
    assert ответ["slots"], "сетка пуста"
    будущее = [
        datetime.fromisoformat(слот["start_at"])
        for слот in ответ["slots"]
        if datetime.fromisoformat(слот["start_at"]) > datetime.now(UTC)
    ]
    assert будущее, "в сетке нет будущих слотов"
    return будущее[0].isoformat()


def заявка(начало: str, *, имя="Гость", почта="guest@example.com", **замены) -> dict:
    """Тело заявки гостя."""
    тело = {
        "start_at": начало,
        "name": имя,
        "email": почта,
        "timezone": "Asia/Krasnoyarsk",
        "consent": True,
    }
    return тело | замены


class TestСсылка:
    async def test_вид_встречи_открывается_по_ссылке(self, клиент_приложения, эксперт):
        ответ = await клиент_приложения.get(f"/api/{ЛОГИН}/{SLUG}")
        assert ответ.status_code == 200
        тело = ответ.json()
        assert тело["name"] == "Консультация по проекту"
        assert тело["duration_minutes"] == 30

    async def test_чужая_ссылка_даёт_404(self, клиент_приложения, эксперт):
        assert (await клиент_приложения.get(f"/api/{ЛОГИН}/net-takoy")).status_code == 404

    async def test_чужой_логин_даёт_404(self, клиент_приложения, эксперт):
        ответ = await клиент_приложения.get(f"/api/kto-to-drugoi/{SLUG}")
        assert ответ.status_code == 404

    async def test_гость_не_нужен(self, клиент_приложения, эксперт):
        # Никакой куки: страница публичная.
        ответ = await клиент_приложения.get(f"/api/{ЛОГИН}/{SLUG}")
        assert "session" not in ответ.request.headers.get("cookie", "")


class TestСетка:
    async def test_сетка_непустая(self, клиент_приложения, эксперт):
        ответ = await клиент_приложения.get(f"/api/{ЛОГИН}/{SLUG}/slots")
        assert ответ.status_code == 200
        assert ответ.json()["slots"]

    async def test_слоты_в_поясе_эксперта(self, клиент_приложения, эксперт):
        # Слот отдаётся в UTC, но по времени гостя должен узнать рабочие часы.
        ответ = (await клиент_приложения.get(f"/api/{ЛОГИН}/{SLUG}/slots")).json()
        часы = {
            datetime.fromisoformat(слот["start_at"]).astimezone(КРАСНОЯРСК).time()
            for слот in ответ["slots"]
        }
        assert min(часы).hour >= 9
        assert max(часы).hour < 12

    async def test_слишком_длинный_диапазон_отклоняется(
        self, клиент_приложения, эксперт
    ):
        ответ = await клиент_приложения.get(f"/api/{ЛОГИН}/{SLUG}/slots?дней=61")
        assert ответ.status_code == 422

    async def test_выключенный_вид_не_открывается(self, клиент_приложения, эксперт, сессия):
        вид = (await сессия.execute(select(EventType))).scalar_one()
        вид.active = False
        await сессия.flush()
        ответ = await клиент_приложения.get(f"/api/{ЛОГИН}/{SLUG}/slots")
        assert ответ.status_code == 404


class TestЗаявка:
    async def test_заявка_принимается(self, клиент_приложения, эксперт):
        начало = await будущий_слот(клиент_приложения)
        ответ = await клиент_приложения.post(
            f"/api/{ЛОГИН}/{SLUG}/bookings", json=заявка(начало)
        )
        assert ответ.status_code == 201
        assert ответ.json()["status"] == "pending"

    async def test_заявка_создаёт_сеанс(self, клиент_приложения, эксперт, сессия):
        начало = await будущий_слот(клиент_приложения)
        await клиент_приложения.post(f"/api/{ЛОГИН}/{SLUG}/bookings", json=заявка(начало))
        from backend.models import Session

        assert (await сессия.execute(select(Session))).scalars().all()

    async def test_время_вне_сетки_отклоняется(self, клиент_приложения, эксперт):
        # `10:07` в расписании нет: инвариант И6.
        чужое = datetime(2026, 10, 6, 7, 7, tzinfo=UTC).isoformat()
        ответ = await клиент_приложения.post(
            f"/api/{ЛОГИН}/{SLUG}/bookings", json=заявка(чужое)
        )
        assert ответ.status_code == 422

    async def test_без_согласия_отклоняется(self, клиент_приложения, эксперт):
        начало = await будущий_слот(клиент_приложения)
        ответ = await клиент_приложения.post(
            f"/api/{ЛОГИН}/{SLUG}/bookings", json=заявка(начало, consent=False)
        )
        assert ответ.status_code == 422

    async def test_без_почты_отклоняется(self, клиент_приложения, эксперт):
        начало = await будущий_слот(клиент_приложения)
        ответ = await клиент_приложения.post(
            f"/api/{ЛОГИН}/{SLUG}/bookings", json=заявка(начало, почта="не-почта")
        )
        assert ответ.status_code == 422

    async def test_время_без_пояса_отклоняется(self, клиент_приложения, эксперт):
        # Время без часового пояса не с чем сравнивать: инвариант И7.
        начало = await будущий_слот(клиент_приложения)
        голое = datetime.fromisoformat(начало).replace(tzinfo=None).isoformat()
        ответ = await клиент_приложения.post(
            f"/api/{ЛОГИН}/{SLUG}/bookings", json=заявка(голое)
        )
        assert ответ.status_code == 422


class TestЗащитаОтПовторов:
    async def test_повторная_заявка_отклоняется(
        self, клиент_приложения, эксперт, сессия
    ):
        # Гость отправил форму дважды, потому что не увидел первую заявку.
        # Инвариант И15: две заявки от одного гостя на один слот не появляются.
        начало = await будущий_слот(клиент_приложения)
        первый = await клиент_приложения.post(
            f"/api/{ЛОГИН}/{SLUG}/bookings", json=заявка(начало)
        )
        assert первый.status_code == 201

        второй = await клиент_приложения.post(
            f"/api/{ЛОГИН}/{SLUG}/bookings", json=заявка(начало)
        )
        assert второй.status_code == 409

        строки = (await сессия.execute(select(Booking))).scalars().all()
        assert len(строки) == 1

    async def test_разный_регистр_почты_считается_тем_же_гостем(
        self, клиент_приложения, эксперт
    ):
        # `Иван@Mail.ru` и `иван@mail.ru` это один человек, иначе он занимал бы
        # два места в одном слоте.
        начало = await будущий_слот(клиент_приложения)
        await клиент_приложения.post(
            f"/api/{ЛОГИН}/{SLUG}/bookings", json=заявка(начало, почта="Ivan@Mail.ru")
        )
        второй = await клиент_приложения.post(
            f"/api/{ЛОГИН}/{SLUG}/bookings", json=заявка(начало, почта=" ivan@mail.ru ")
        )
        assert второй.status_code == 409

    async def test_второй_гость_на_тот_же_слот_отклоняется(
        self, клиент_приложения, эксперт, сессия
    ):
        # Главное правило MVP: один слот это один гость. Второй получает 409.
        начало = await будущий_слот(клиент_приложения)
        первый = await клиент_приложения.post(
            f"/api/{ЛОГИН}/{SLUG}/bookings", json=заявка(начало, почта="first@mail.ru")
        )
        assert первый.status_code == 201

        второй = await клиент_приложения.post(
            f"/api/{ЛОГИН}/{SLUG}/bookings", json=заявка(начало, почта="second@mail.ru")
        )
        assert второй.status_code == 409
        assert "записан другой" in второй.json()["detail"]

        строки = (await сессия.execute(select(Booking))).scalars().all()
        assert len(строки) == 1, "второй заявки в базе быть не должно"

    async def test_разные_слоты_занимаются_разными_гостями(
        self, клиент_приложения, эксперт
    ):
        # Правило относится к одному слоту, а не ко всей встрече.
        первый = await будущий_слот(клиент_приложения)
        ответ = await клиент_приложения.post(
            f"/api/{ЛОГИН}/{SLUG}/bookings", json=заявка(первый, почта="a@mail.ru")
        )
        assert ответ.status_code == 201

        сетка = (await клиент_приложения.get(f"/api/{ЛОГИН}/{SLUG}/slots")).json()
        свободный = next(
            слот["start_at"]
            for слот in сетка["slots"]
            if слот["can_request"] and слот["start_at"] != первый
        )
        второй = await клиент_приложения.post(
            f"/api/{ЛОГИН}/{SLUG}/bookings",
            json=заявка(свободный, почта="b@mail.ru"),
        )
        assert второй.status_code == 201

    async def test_занятый_слот_исчезает_из_доступных(
        self, клиент_приложения, эксперт
    ):
        начало = await будущий_слот(клиент_приложения)
        await клиент_приложения.post(f"/api/{ЛОГИН}/{SLUG}/bookings", json=заявка(начало))

        ответ = (await клиент_приложения.get(f"/api/{ЛОГИН}/{SLUG}/slots")).json()
        нужный = next(
            слот for слот in ответ["slots"] if слот["start_at"] == начало
        )
        assert нужный["is_taken"] is True
        assert нужный["can_request"] is False


class TestПриватность:
    async def test_почта_в_базе_зашифрована(
        self, клиент_приложения, эксперт, сессия
    ):
        from sqlalchemy import text

        начало = await будущий_слот(клиент_приложения)
        await клиент_приложения.post(
            f"/api/{ЛОГИН}/{SLUG}/bookings", json=заявка(начало, почта="secret@mail.ru")
        )

        сырое = (
            await сессия.execute(text("select guest_email from bookings limit 1"))
        ).scalar_one()
        assert b"secret@mail.ru" not in сырое

    async def test_слепой_индекс_ищет_по_почте(
        self, клиент_приложения, эксперт, сессия
    ):
        # По зашифрованной колонке искать нельзя, поиск идёт по индексу.
        начало = await будущий_слот(клиент_приложения)
        await клиент_приложения.post(
            f"/api/{ЛОГИН}/{SLUG}/bookings", json=заявка(начало, почта="Find@Mail.ru")
        )

        найдено = (
            await сессия.execute(
                select(Booking).where(
                    Booking.guest_email_bidx == слепой_индекс("find@mail.ru")
                )
            )
        ).scalars().all()
        assert len(найдено) == 1

    async def test_гость_видит_только_себя(self, клиент_приложения, эксперт):
        # Страница вида встречи не содержит чужих заявок.
        ответ = (await клиент_приложения.get(f"/api/{ЛОГИН}/{SLUG}")).json()
        assert "bookings" not in ответ
        assert "guest_email" not in ответ


class TestПанельЭксперта:
    @pytest.fixture(autouse=True)
    async def _вошедший(self, клиент_приложения, эксперт, сессия):
        """Войти как эксперт: панель доступна только вошедшему."""
        from backend.models import AuthSession
        from backend.session_tokens import generate_token, hash_token, new_session_expiry

        токен = generate_token()
        сессия.add(
            AuthSession(
                user_id=эксперт.id,
                token_hash=hash_token(токен),
                expires_at=new_session_expiry(),
            )
        )
        await сессия.flush()
        клиент_приложения.cookies.set("session", токен)

    async def test_без_входа_панель_закрыта(self, клиент_приложения):
        клиент_приложения.cookies.clear()
        assert (await клиент_приложения.get("/api/panel/bookings")).status_code == 401

    async def test_список_заявок_показывает_гостя(
        self, клиент_приложения, эксперт
    ):
        начало = await будущий_слот(клиент_приложения)
        await клиент_приложения.post(
            f"/api/{ЛОГИН}/{SLUG}/bookings", json=заявка(начало, имя="Пётр", почта="petr@mail.ru")
        )

        ответ = await клиент_приложения.get("/api/panel/bookings")
        assert ответ.status_code == 200
        строки = ответ.json()
        assert len(строки) == 1
        assert строки[0]["name"] == "Пётр"
        assert строки[0]["email"] == "petr@mail.ru"
        assert строки[0]["status"] == "pending"

    async def test_у_эксперта_есть_публичная_ссылка(self, клиент_приложения, эксперт):
        ответ = await клиент_приложения.get("/api/panel/event-types")
        assert ответ.status_code == 200
        виды = ответ.json()
        assert виды[0]["public_path"] == f"/u/{ЛОГИН}/{SLUG}"

    async def test_новый_вид_встречи_создаётся(self, клиент_приложения, эксперт):
        ответ = await клиент_приложения.post(
            "/api/panel/event-types",
            json={
                "name": "Код-ревью",
                "slug": "kod-review",
                "duration_minutes": 60,
                "schedule": {
                    "weekdays": [2, 4],
                    "start_time": "14:00",
                    "end_time": "18:00",
                },
            },
        )
        assert ответ.status_code == 201
        assert ответ.json()["public_path"] == f"/u/{ЛОГИН}/kod-review"

    async def test_расписание_переиспользуется(self, клиент_приложения, эксперт, сессия):
        # Часы приёма общие: второй вид встречи не создаёт второе расписание.
        await клиент_приложения.post(
            "/api/panel/event-types",
            json={
                "name": "Код-ревью",
                "slug": "kod-review",
                "duration_minutes": 60,
                "schedule": {
                    "weekdays": [2, 4],
                    "start_time": "14:00",
                    "end_time": "18:00",
                },
            },
        )
        расписания = (await сессия.execute(select(Schedule))).scalars().all()
        assert len(расписания) == 1
        assert расписания[0].weekdays == "2,4"
        assert расписания[0].start_time == "14:00"

    async def test_одинаковая_ссылка_даёт_409(self, клиент_приложения, эксперт):
        ответ = await клиент_приложения.post(
            "/api/panel/event-types",
            json={
                "name": "Второе",
                "slug": SLUG,
                "duration_minutes": 30,
                "schedule": {
                    "weekdays": [1],
                    "start_time": "09:00",
                    "end_time": "12:00",
                },
            },
        )
        assert ответ.status_code == 409

    async def test_кириллица_в_ссылке_отклоняется(self, клиент_приложения, эксперт):
        ответ = await клиент_приложения.post(
            "/api/panel/event-types",
            json={
                "name": "Консультация",
                "slug": "консультация",
                "duration_minutes": 30,
                "schedule": {
                    "weekdays": [1],
                    "start_time": "09:00",
                    "end_time": "12:00",
                },
            },
        )
        assert ответ.status_code == 422

    async def test_пустое_окно_отклоняется(self, клиент_приложения, эксперт):
        # Начало позже конца: инвариант И3.
        ответ = await клиент_приложения.post(
            "/api/panel/event-types",
            json={
                "name": "Перепутанное",
                "slug": "perputannoe",
                "duration_minutes": 30,
                "schedule": {
                    "weekdays": [1],
                    "start_time": "18:00",
                    "end_time": "09:00",
                },
            },
        )
        assert ответ.status_code == 422
