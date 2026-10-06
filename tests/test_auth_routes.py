"""Тесты маршрутов входа и выхода.

Проверяется граница приложения: куда и с какими параметрами отправляется
эксперт, что происходит с его ответом и что остаётся в базе после входа.

Сеть не используется. Обмен с Яндексем подменён целиком: тесты отвечают тем,
что вернул бы Яндекс, и не трогают код клиента.
"""

import httpx
import pytest
from sqlalchemy import select, text

from backend.config import get_settings
from backend.models import AuthSession, OAuthToken, User
from backend.session_tokens import hash_token
from backend.yandex.client import USERINFO_URL

# Адрес интерфейса из настроек. Читаем его здесь, а не пишем константой,
# чтобы тест проверял настоящее поведение, а не совпадение с зашитым адресом.
БАЗА_ИНТЕРФЕЙСА = get_settings().app_base_url.rstrip("/")

ПРОФИЛЬ = {
    "id": "1000034427",
    "login": "shkutanmaxaon",
    "display_name": "Максим",
    "real_name": "Шкутан Максим",
    "default_email": "shkutanmaxaon@yandex.ru",
}

ОТВЕТ_ТОКЕНА = {
    "token_type": "bearer",
    "access_token": "accessTokenAq1b2C3",
    "refresh_token": "refreshTokenXy9z8",
    "expires_in": 3600,
    "scope": "login:email login:info",
}


def яндекс_отвечает(*, токен: dict | None = None, профиль: dict | None = None) -> None:
    """Подмена транспорта: ответы Яндекса на обмен и на профиль."""

    async def обработчик(запрос: httpx.Request) -> httpx.Response:
        if запрос.method == "POST":
            тело = токен if токен is not None else ОТВЕТ_ТОКЕНА
            # Ошибку Яндекс отдаёт не телом, а статусом: код 200 с телом ошибки
            # он не присылает, и проверять такую подмену было бы проверкой
            # несуществующего у Яндекса случая.
            статус = 400 if "error" in тело else 200
            return httpx.Response(статус, json=тело)
        assert str(запрос.url).startswith(USERINFO_URL)
        return httpx.Response(200, json=профиль if профиль is not None else ПРОФИЛЬ)

    return httpx.MockTransport(обработчик)


@pytest.fixture
def подменяем_яндекса(приложение):
    """Заменить обмен с Яндексом на ответы из теста.

    Подмена идёт через `dependency_overrides` самого приложения, а не через
    `monkeypatch` по модулю: обработчик импортирует `get_http_client` при
    загрузке, и подмена исходного модуля до него уже не доходит.
    """
    from backend.yandex.client import get_http_client

    def установить(*, токен=None, профиль=None):
        транспорт = яндекс_отвечает(токен=токен, профиль=профиль)
        клиент = httpx.AsyncClient(transport=транспорт)

        async def подмена():
            yield клиент

        приложение.dependency_overrides[get_http_client] = подмена

    return установить


async def начять_вход(клиент) -> httpx.Response:
    """Первый шаг входа: получить адрес Яндекса и временную куку."""
    ответ = await клиент.get("/auth/yandex", follow_redirects=False)
    assert ответ.status_code == 307
    return ответ


async def завершить_вход(клиент, *, state: str | None = None) -> httpx.Response:
    """Второй шаг: вернуться от Яндекса с кодом и тем же state."""
    начало = await начять_вход(клиент)
    if state is None:
        state = начало.headers["location"].split("state=")[1].split("&")[0]
    return await клиент.get(
        "/auth/yandex/callback",
        params={"code": "код-подтверждения", "state": state},
        follow_redirects=False,
    )


class TestНачалоВхода:
    async def test_отправляет_на_яндекс(self, клиент_приложения):
        ответ = await начять_вход(клиент_приложения)
        assert ответ.headers["location"].startswith("https://oauth.yandex.ru/authorize")

    async def test_адрес_содержит_нужные_параметры(self, клиент_приложения):
        ответ = await начять_вход(клиент_приложения)
        адрес = ответ.headers["location"]
        assert "response_type=code" in адрес
        assert "client_id=" in адрес
        assert "state=" in адрес

    async def test_пустой_список_прав_не_отправляется(
        self, клиент_приложения, приложение
    ):
        # Права Телемоста нельзя запрашивать, пока их нет у приложения:
        # Яндекс отвечает `invalid_scope` и отменяет всю авторизацию. Пустой
        # список поэтому не отправляется вовсе, даже отдельным параметром.
        пусто = get_settings().model_copy(update={"yandex_optional_scope": ""})
        приложение.dependency_overrides[get_settings] = lambda: пусто
        try:
            ответ = await начять_вход(клиент_приложения)
        finally:
            приложение.dependency_overrides.pop(get_settings)

        assert "optional_scope" not in ответ.headers["location"]

    async def test_права_телемоста_добавляются_к_запросу(
        self, клиент_приложения, приложение
    ):
        # Как только права появятся в настройке, они должны уйти в адрес.
        # Проверяем настройку, а не само значение: состав прав задаёт Яндекс.
        настройки = get_settings().model_copy(
            update={"yandex_optional_scope": "telemost-api:conferences.create"}
        )
        приложение.dependency_overrides[get_settings] = lambda: настройки
        try:
            ответ = await начять_вход(клиент_приложения)
        finally:
            приложение.dependency_overrides.pop(get_settings)
        assert "optional_scope=telemost-api%3Aconferences.create" in ответ.headers[
            "location"
        ]

    async def test_каждый_раз_новый_state(self, клиент_приложения):
        # Иначе подделать ответ авторизации было бы легко.
        первый = await начять_вход(клиент_приложения)
        второй = await начять_вход(клиент_приложения)
        assert первый.headers["location"] != второй.headers["location"]

    async def test_временная_кука_не_доступна_скриптам(self, клиент_приложения):
        # В куке лежит code_verifier, его нельзя отдать в распоряжение скриптов.
        ответ = await начять_вход(клиент_приложения)
        assert "httponly" in ответ.headers["set-cookie"].lower()


class TestОшибкиВхода:
    async def test_без_кода_даёт_400(self, клиент_приложения):
        ответ = await клиент_приложения.get(
            "/auth/yandex/callback", params={"state": "любое"}
        )
        assert ответ.status_code == 400

    async def test_отказ_пользователя_даёт_400(self, клиент_приложения):
        ответ = await клиент_приложения.get(
            "/auth/yandex/callback",
            params={"error": "access_denied", "state": "любое"},
        )
        assert ответ.status_code == 400

    async def test_незарегистрированные_права_дают_500(self, клиент_приложения):
        # Прав, которых нет у приложения, нет и у человека: он ничего не мог
        # сделать неправильно. Отвечать «вы не разрешили доступ» здесь нельзя,
        # человек станет искать у себя проблему, которой нет.
        ответ = await клиент_приложения.get(
            "/auth/yandex/callback",
            params={"error": "invalid_scope", "state": "любое"},
        )
        assert ответ.status_code == 500
        assert "прав" in ответ.json()["detail"]

    async def test_чужой_state_даёт_403(self, клиент_приложения, подменяем_яндекса):
        # Человеку подсунули ссылку с чужим кодом. Ответ принимать нельзя.
        подменяем_яндекса()
        ответ = await завершить_вход(клиент_приложения, state="чужое-значение")
        assert ответ.status_code == 403

    async def test_без_временной_куки_даёт_403(self, клиент_приложения, подменяем_яндекса):
        # Временная кука с верификатором исчезла, код уже не обменять.
        подменяем_яндекса()
        ответ = await клиент_приложения.get(
            "/auth/yandex/callback",
            params={"code": "код", "state": "какое-угодно"},
        )
        assert ответ.status_code == 403

    async def test_подделанный_код_даёт_502(self, клиент_приложения, подменяем_яндекса):
        # Яндекс отверг код. Это сбой на его стороне, а не наша ошибка.
        подменяем_яндекса(токен={"error": "invalid_grant"})
        ответ = await завершить_вход(клиент_приложения)
        assert ответ.status_code == 502

    async def test_неполный_профиль_даёт_502(self, клиент_приложения, подменяем_яндекса):
        # Без логина не найти пользователя и построить почту. Отвечаем 502, а не
        # 500: у нас всё в порядке, плохие данные прислал внешний сервис.
        подменяем_яндекса(профиль={"id": "1", "default_email": "a@yandex.ru"})
        ответ = await завершить_вход(клиент_приложения)
        assert ответ.status_code == 502


class TestУспешныйВход:
    async def test_ведёт_в_профиль(self, клиент_приложения, подменяем_яндекса):
        # Адрес собирается из APP_BASE_URL: Яндекс возвращает человека на
        # сервис, а страница профиля живёт на интерфейсе, и в разработке это
        # разные порты.
        подменяем_яндекса()
        ответ = await завершить_вход(клиент_приложения)
        assert ответ.status_code == 307
        assert ответ.headers["location"] == f"{БАЗА_ИНТЕРФЕЙСА}/profile"

    async def test_выдаёт_куку_сессии(self, клиент_приложения, подменяем_яндекса):
        подменяем_яндекса()
        ответ = await завершить_вход(клиент_приложения)
        assert "session=" in ответ.headers["set-cookie"]

    async def test_кука_сессии_защищена(self, клиент_приложения, подменяем_яндекса):
        # HttpOnly убирает токен из поля зрения скриптов, SameSite=Lax не даёт
        # утащить его со стороннего сайта.
        подменяем_яндекса()
        ответ = await завершить_вход(клиент_приложения)
        кука = ответ.headers["set-cookie"].lower()
        assert "httponly" in кука
        assert "samesite=lax" in кука

    async def test_создаёт_эксперта(self, клиент_приложения, подменяем_яндекса, сессия):
        подменяем_яндекса()
        await завершить_вход(клиент_приложения)
        эксперт = (await сессия.execute(select(User))).scalar_one()
        assert эксперт.yandex_id == "1000034427"
        assert эксперт.login == "shkutanmaxaon"
        assert эксперт.email == "shkutanmaxaon@yandex.ru"

    async def test_первый_вошедший_становится_организатором(
        self, клиент_приложения, подменяем_яндекса, сессия
    ):
        подменяем_яндекса()
        await завершить_вход(клиент_приложения)
        эксперт = (await сессия.execute(select(User))).scalar_one()
        assert эксперт.role == "organizer"

    async def test_второй_вошедший_не_получает_админа(
        self, клиент_приложения, подменяем_яндекса, сессия
    ):
        # Роль выдаётся один раз. Автоматически повысить кого-то до администратора
        # нельзя: пришёл новый человек и стал бы админом без чьего-то решения.
        подменяем_яндекса()
        await завершить_вход(клиент_приложения)
        await клиент_приложения.post("/auth/logout")

        подменяем_яндекса(
            профиль={
                "id": "2000000001",
                "login": "second",
                "default_email": "second@yandex.ru",
            }
        )
        await завершить_вход(клиент_приложения)

        эксперты = (await сессия.execute(select(User))).scalars().all()
        assert {э.role for э in эксперты} == {"organizer"}

    async def test_повторный_вход_не_создаёт_дубль(
        self, клиент_приложения, подменяем_яндекса, сессия
    ):
        # Один и тот же человек приходит с другого устройства, а не появляется
        # второй раз в базе.
        подменяем_яндекса()
        await завершить_вход(клиент_приложения)
        await клиент_приложения.post("/auth/logout")
        await завершить_вход(клиент_приложения)

        эксперты = (await сессия.execute(select(User))).scalars().all()
        assert len(эксперты) == 1


class TestЧтоОстаётсяВБазе:
    async def test_в_базе_хеш_а_не_токен(
        self, клиент_приложения, подменяем_яндекса, сессия
    ):
        # Утечка дампа не должна давать возможность войти. Поэтому в базе лежит
        # только HMAC, из которого токен не восстановить.
        подменяем_яндекса()
        ответ = await завершить_вход(клиент_приложения)
        токен = ответ.headers["set-cookie"].split("session=")[1].split(";")[0]

        сессия_входа = (await сессия.execute(select(AuthSession))).scalar_one()
        assert токен not in сессия_входа.token_hash
        assert сессия_входа.token_hash == hash_token(токен)

    async def test_токен_яндекса_в_базе_зашифрован(
        self, клиент_приложения, подменяем_яндекса, сессия
    ):
        # Токен даёт доступ к аккаунту эксперта. В базе он не лежит открытым.
        подменяем_яндекса()
        await завершить_вход(клиент_приложения)

        строка = (await сессия.execute(select(OAuthToken))).scalar_one()
        сырое = (
            await сессия.execute(text("select access_token from oauth_tokens limit 1"))
        ).scalar_one()
        assert ОТВЕТ_ТОКЕНА["access_token"].encode() not in сырое
        assert строка.access_token == ОТВЕТ_ТОКЕНА["access_token"]

    async def test_срок_сессии_семь_дней(
        self, клиент_приложения, подменяем_яндекса, сессия
    ):
        from datetime import UTC, datetime, timedelta

        подменяем_яндекса()
        await завершить_вход(клиент_приложения)
        сессия_входа = (await сессия.execute(select(AuthSession))).scalar_one()
        осталось = сессия_входа.expires_at - datetime.now(UTC)
        assert timedelta(days=6) < осталось < timedelta(days=7, minutes=1)

    async def test_старый_вход_остаётся_рабочим(
        self, клиент_приложения, подменяем_яндекса, сессия
    ):
        # Эксперт входит с компьютера, потом с телефона. Оба входа остаются
        # рабочими: сессия привязана к устройству, а не к человеку.
        подменяем_яндекса()
        await завершить_вход(клиент_приложения)
        первый = клиент_приложения.cookies.get("session")

        подменяем_яндекса()
        await завершить_вход(клиент_приложения)

        assert len((await сессия.execute(select(AuthSession))).scalars().all()) == 2

        клиент_приложения.cookies.set("session", первый)
        assert (await клиент_приложения.get("/auth/me")).status_code == 200


class TestВыход:
    async def test_удаляет_сессию(self, клиент_приложения, подменяем_яндекса, сессия):
        подменяем_яндекса()
        await завершить_вход(клиент_приложения)
        await клиент_приложения.post("/auth/logout")
        assert (await сессия.execute(select(AuthSession))).scalars().all() == []

    async def test_чистит_куку(self, клиент_приложения, подменяем_яндекса):
        подменяем_яндекса()
        await завершить_вход(клиент_приложения)
        ответ = await клиент_приложения.post("/auth/logout")
        assert "session=" in ответ.headers["set-cookie"]

    async def test_старая_кука_больше_не_работает(
        self, клиент_приложения, подменяем_яндекса
    ):
        # Выход действует немедленно, а не по истечении срока.
        подменяем_яндекса()
        await завершить_вход(клиент_приложения)
        токен = клиент_приложения.cookies.get("session")
        await клиент_приложения.post("/auth/logout")

        клиент_приложения.cookies.set("session", токен)
        ответ = await клиент_приложения.get("/auth/me")
        assert ответ.status_code == 401