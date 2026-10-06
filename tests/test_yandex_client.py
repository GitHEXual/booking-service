"""Тесты OAuth-клиента Яндекс ID.

Сеть не используется. Тесты проверяют границу с Яндексом: правильно ли собран
запрос, как разбирается ответ и во что превращаются ошибки.

Подменяется транспорт httpx, а не код приложения. Поэтому проверки идут по
тому, что клиент действительно отправил, а не по тому, как он это устроил.
"""

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from backend.yandex.client import (
    YandexAuthDeniedError,
    YandexOAuthError,
    build_authorize_url,
    exchange_code,
    fetch_profile,
    new_code_verifier,
    new_state,
    refresh_access_token,
    verify_state,
)

CLIENT_ID = "a1b2c3d4"
CLIENT_SECRET = "секрет-приложения"
REDIRECT_URI = "http://localhost:8000/auth/yandex/callback"

SCOPE = "login:email login:info"
OPTIONAL_SCOPE = "telemost-api:conferences.create"

# Ответ Яндекса на обмен кода. `expires_in` задан заранее, чтобы не ждать.
ОТВЕТ_ТОКЕНА = {
    "token_type": "bearer",
    "access_token": "токен-доступа",
    "refresh_token": "токен-обновления",
    "expires_in": 3600,
    "scope": "login:email login:info",
}


def клиент(обработчик) -> httpx.AsyncClient:
    """Клиент httpx, отправляющий запросы в переданный обработчик."""
    return httpx.AsyncClient(transport=httpx.MockTransport(обработчик))


def s256(значение: str) -> str:
    """Challenge по RFC 7636: base64url от sha256 без знака выравнивания."""
    дайджест = hashlib.sha256(значение.encode()).digest()
    return base64.urlsafe_b64encode(дайджест).decode().rstrip("=")


async def обменяй_код(http: httpx.AsyncClient, **переопределения) -> object:
    """Обменять код на токен с параметрами по умолчанию."""
    параметры = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "code": "код-подтверждения",
        "code_verifier": "верификатор",
    }
    return await exchange_code(http, **параметры | переопределения)


class TestState:
    def test_state_не_пустой(self):
        assert new_state()

    def test_state_каждый_раз_новый(self):
        assert new_state() != new_state()

    def test_state_достаточно_длинный(self):
        # Короткий state легко угадать, а он защищает от подделки ответа
        # авторизации. Длиннее не нужно: Яндекс принимает до 1024 символов.
        assert len(new_state()) >= 32


class TestCodeVerifier:
    def test_верификатор_в_допустимой_длине(self):
        # RFC 7636 требует от 43 до 128 символов.
        assert 43 <= len(new_code_verifier()) <= 128

    def test_верификатор_каждый_раз_новый(self):
        assert new_code_verifier() != new_code_verifier()


class TestAuthorizeUrl:
    def адрес(self, **переопределения) -> str:
        параметры = {
            "state": "уникальное-значение",
            "code_verifier": "верификатор",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
        }
        return build_authorize_url(**параметры | переопределения)

    def параметры(self, **переопределения) -> dict[str, list[str]]:
        разобранный = urlparse(self.адрес(**переопределения))
        return parse_qs(разобранный.query)

    def test_адрес_это_янекс(self):
        разобранный = urlparse(self.адрес())
        assert разобранный.scheme == "https"
        assert разобранный.netloc == "oauth.yandex.ru"

    def test_содержит_идентификатор_приложения(self):
        assert self.параметры()["client_id"] == [CLIENT_ID]

    def test_запрашивает_код_а_не_токен(self):
        # `response_type=token` вернул бы токен во фрагменте адреса, и его
        # пришлось бы вылавливать на клиенте. Нужен код, чтобы обменять его
        # на токен у себя на сервере.
        assert self.параметры()["response_type"] == ["code"]

    def test_указывает_адрес_возврата(self):
        assert self.параметры()["redirect_uri"] == [REDIRECT_URI]

    def test_права_на_вход_обязательные(self):
        assert self.параметры()["scope"] == [SCOPE]

    def test_права_на_встречи_необязательные(self):
        # Права Телемоста необязательные: человек может отказаться от видео и
        # всё равно получить доступ к панели. Обязательными они стали бы
        # отказом от входа целиком.
        параметры = self.параметры(optional_scope=OPTIONAL_SCOPE)
        assert параметры["optional_scope"] == [OPTIONAL_SCOPE]
        assert "telemost" not in параметры["scope"][0]

    def test_передаёт_state(self):
        параметры = self.параметры(state="уникальное-значение")
        assert параметры["state"] == ["уникальное-значение"]

    def test_передаёт_challenge_а_не_верификатор(self):
        # Отправлять сам верификатор бессмысленно: он предназначен для обмена
        # кодом на токен. В адрес авторизации уходит только производное от него.
        верификатор = new_code_verifier()
        параметры = self.параметры(code_verifier=верификатор)
        assert параметры["code_challenge"] == [s256(верификатор)]
        assert верификатор not in параметры["code_challenge"][0]

    def test_метод_преобразования_выбран_s256(self):
        assert self.параметры()["code_challenge_method"] == ["S256"]


class TestVerifyState:
    def test_совпадающий_state_принимается(self):
        assert verify_state("ожидаем", "ожидаем") is True

    def test_разный_state_отклоняется(self):
        # Защита от подделки ответа авторизации: если человеку подсунули ссылку
        # с чужим кодом, его state не совпадёт.
        assert verify_state("ожидаем", "чужое") is False

    def test_отсутствие_state_отклоняется(self):
        assert verify_state("", "ожидаем") is False
        assert verify_state("ожидаем", None) is False


class TestExchangeCode:
    async def test_шлёт_post(self):
        видел = {}

        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            видел["метод"] = запрос.method
            return httpx.Response(200, json=ОТВЕТ_ТОКЕНА)

        async with клиент(обработчик) as http:
            await обменяй_код(http)

        assert видел["метод"] == "POST"

    async def test_тело_это_форма_а_не_json(self):
        # Яндекс принимает только `application/x-www-form-urlencoded`. С JSON
        # вместо формы пришлось бы разбираться с ошибкой формата вместо
        # нормального ответа.
        видел = {}

        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            видел["тип"] = запрос.headers.get("content-type", "")
            return httpx.Response(200, json=ОТВЕТ_ТОКЕНА)

        async with клиент(обработчик) as http:
            await обменяй_код(http)

        assert видел["тип"] == "application/x-www-form-urlencoded"

    async def test_в_теле_нужные_поля(self):
        видел = {}

        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            видел["тело"] = запрос.read().decode()
            return httpx.Response(200, json=ОТВЕТ_ТОКЕНА)

        async with клиент(обработчик) as http:
            await обменяй_код(http)

        тело = видел["тело"]
        assert "grant_type=authorization_code" in тело
        assert "code_verifier=" in тело
        assert "client_id=a1b2c3d4" in тело

    async def test_возвращает_токен_из_ответа(self):
        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=ОТВЕТ_ТОКЕНА)

        async with клиент(обработчик) as http:
            токен = await обменяй_код(http)

        assert токен.access_token == "токен-доступа"
        assert токен.refresh_token == "токен-обновления"

    async def test_срок_считается_из_expires_in(self):
        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={**ОТВЕТ_ТОКЕНА, "expires_in": 600})

        async with клиент(обработчик) as http:
            токен = await обменяй_код(http)

        # Срок токена приходит от Яндекса, а из него вычитается запас на
        # сетевую задержку. Здесь 600 минуты означают 540 секунд.
        осталось = токен.expires_at - datetime.now(UTC)
        assert timedelta(minutes=8) < осталось < timedelta(minutes=11)

    async def test_отказ_пользователя_понятен(self):
        # Человек нажал «не разрешаю». Это не поломка сервиса, а отказ, и
        # сообщение должно быть об этом.
        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "access_denied"})

        async with клиент(обработчик) as http:
            with pytest.raises(YandexAuthDeniedError):
                await обменяй_код(http)

    async def test_подделанный_код_даёт_понятную_ошибку(self):
        # `invalid_grant` означает, что код не тот или истёк. Это ожидаемый
        # исход при попытке подсунуть свой код, а не сбой.
        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "invalid_grant"})

        async with клиент(обработчик) as http:
            with pytest.raises(YandexOAuthError):
                await обменяй_код(http)

    async def test_недоступность_яндекса_не_путается_с_отказом(self):
        # Сеть легла. Человек ничего не делал, и показывать ему «вы отказали
        # доступ» нельзя.
        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("сеть недоступна")

        async with клиент(обработчик) as http:
            with pytest.raises(YandexOAuthError):
                await обменяй_код(http)

    async def test_секрет_не_попадает_в_текст_ошибки(self):
        # Если секрет приложения окажется в сообщении об ошибке, журнал станет
        # источником секретов.
        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "invalid_client"})

        async with клиент(обработчик) as http:
            with pytest.raises(YandexOAuthError) as ошибка:
                await обменяй_код(http)

        assert CLIENT_SECRET not in str(ошибка.value)


class TestRefreshAccessToken:
    async def test_шлёт_refresh_token(self):
        видел = {}

        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            видел["тело"] = запрос.read().decode()
            return httpx.Response(200, json=ОТВЕТ_ТОКЕНА)

        async with клиент(обработчик) as http:
            await refresh_access_token(
                http,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                refresh_token="старый-токен",
            )

        assert "grant_type=refresh_token" in видел["тело"]
        assert "refresh_token=" in видел["тело"]

    async def test_возвращает_новый_токен(self):
        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={**ОТВЕТ_ТОКЕНА, "access_token": "новый"})

        async with клиент(обработчик) as http:
            токен = await refresh_access_token(
                http,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                refresh_token="старый-токен",
            )

        assert токен.access_token == "новый"

    async def test_протухший_refresh_токен_даёт_ошибку(self):
        # Значит человека надо отправить входить заново. Это не повод
        # повторять попытку по кругу.
        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "invalid_grant"})

        async with клиент(обработчик) as http:
            with pytest.raises(YandexOAuthError):
                await refresh_access_token(
                    http,
                    client_id=CLIENT_ID,
                    client_secret=CLIENT_SECRET,
                    refresh_token="протухший",
                )


class TestFetchProfile:
    async def test_токен_идёт_в_заголовке(self):
        # В параметре URL токен попал бы в журнал веб-сервера и в историю
        # браузера. В заголовке его там нет.
        видел = {}

        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            видел["заголовок"] = запрос.headers.get("authorization", "")
            видел["адрес"] = str(запрос.url)
            return httpx.Response(200, json={"id": "1", "login": "ivan"})

        async with клиент(обработчик) as http:
            await fetch_profile(http, access_token="Aq1b2C3d4E5f6secret")

        assert видел["заголовок"] == "OAuth Aq1b2C3d4E5f6secret"
        assert "Aq1b2C3d4E5f6secret" not in видел["адрес"]

    async def test_запрашивает_json(self):
        видел = {}

        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            видел["формат"] = запрос.url.params.get("format")
            return httpx.Response(200, json={"id": "1", "login": "ivan"})

        async with клиент(обработчик) as http:
            await fetch_profile(http, access_token="Aq1b2C3d4E5f6secret")

        assert видел["формат"] == "json"

    async def test_возвращает_данные_как_есть(self):
        ответ = {"id": "1", "login": "ivan", "default_email": "ivan@yandex.ru"}

        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=ответ)

        async with клиент(обработчик) as http:
            профиль = await fetch_profile(http, access_token="Aq1b2C3d4E5f6secret")

        assert профиль == ответ

    async def test_отказ_яндекса_даёт_ошибку(self):
        async def обработчик(запрос: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        async with клиент(обработчик) as http:
            with pytest.raises(YandexOAuthError):
                await fetch_profile(http, access_token="expiredToken1")