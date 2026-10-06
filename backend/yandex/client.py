"""Клиент OAuth Яндекс ID и профиля.

Обмен с Яндексом идёт четырьмя вызовами: адрес авторизации, обмен кода на
токен, обновление токена и чтение профиля. Клиент httpx передаётся снаружи,
поэтому в тестах подменяется транспорт, а не этот код.

Две особенности, на которых легко ошибиться, зафиксированы здесь же:

- Токен кладётся в заголовок `Authorization` со схемой `OAuth`. В примерах
  документации Яндекса именно `OAuth`, хотя в ответе на обмен поле
  `token_type` содержит `bearer`. Со схемой `Bearer` ответ приходит `401`.
- Обмен кода идёт телом формы, а не JSON. С JSON приходит ошибка формата
  вместо токена.
"""

import base64
import hashlib
import secrets
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
TOKEN_URL = "https://oauth.yandex.ru/token"
USERINFO_URL = "https://login.yandex.ru/info"

DEFAULT_SCOPE = "login:info login:email"

# Права Телемоста нужны, чтобы создавать встречи от имени эксперта, и приходят
# из настройки `YANDEX_OPTIONAL_SCOPE`. Запрашивать их можно только когда они
# есть у приложения: Яндекс отвечает на право, которого нет в перечне при
# регистрации, ошибкой `invalid_scope` и отменяет всю авторизацию. Поэтому
# список пустой, а включается одной строкой в `.env`.
#
# Текущие права приложения: https://oauth.yandex.ru/client/<client_id>/info

# Яндекс отвечает ошибкой и кодом, а не текстом. Отказ человека и отказ по
# неверному коду это разные вещи: первый это его выбор, второй ошибка.
ОТКАЗ_ПОЛЬЗОВАТЕЛЯ = "access_denied"

# Прав, которых нет у приложения. Отличаем от прочих ошибок, потому что
# человек тут ни при чём: дело в настройке приложения, и виноват код.
НЕТ_ТАКИХ_ПРАВ = "invalid_scope"

# Сколько секунд вычитаем из срока токена, чтобы не успеть уехать за границу.
ЗАПАС_ПЕРЕД_ИСТЕЧЕНИЕМ_СЕКУНД = 60

# Сколько ждём ответа Яндекса. Больше не нужно: если Яндекс думает дольше,
# человек всё равно увидит ошибку и попробует ещё раз.
ТАЙМАУТ_СЕКУНД = 10.0


class YandexOAuthError(Exception):
    """Что-то пошло не так при обращении к Яндексу."""


class YandexAuthDeniedError(YandexOAuthError):
    """Человек отказал приложению в доступе."""


@dataclass(frozen=True, slots=True)
class OAuthToken:
    """Токен эксперта с правом создавать встречи от его имени."""

    access_token: str
    refresh_token: str
    expires_at: datetime
    scope: str

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) >= self.expires_at


def new_state() -> str:
    """Значение для защиты от подделки ответа авторизации."""
    return secrets.token_urlsafe(32)


def new_code_verifier() -> str:
    """Секрет расширения PKCE."""
    return secrets.token_urlsafe(64)


def _challenge(верификатор: str) -> str:
    """Преобразование верификатора в challenge по RFC 7636."""
    дайджест = hashlib.sha256(верификатор.encode()).digest()
    return base64.urlsafe_b64encode(дайджест).decode().rstrip("=")


def build_authorize_url(
    *,
    state: str,
    code_verifier: str,
    client_id: str,
    redirect_uri: str,
    scope: str = DEFAULT_SCOPE,
    optional_scope: str = "",
) -> str:
    """Адрес, на который отправляем человека для согласия."""
    параметры = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": _challenge(code_verifier),
        "code_challenge_method": "S256",
    }
    # Пустой `optional_scope` не отправляем: Яндекс считает это запросом
    # дополнительных прав и отвечает `invalid_scope`, даже если список пуст.
    if optional_scope:
        параметры["optional_scope"] = optional_scope
    return f"{AUTHORIZE_URL}?{urlencode(параметры)}"


def verify_state(ожидаемое: str, полученное: str | None) -> bool:
    """Совпал ли state с тем, что мы отправили.

    Сравнение постоянного времени. При несовпадении человек нажал не ту
    ссылку или его обманули, и код из такого ответа принимать нельзя.
    """
    if not ожидаемое or not полученное:
        return False
    # Сравниваем байтами: `compare_digest` отказывается работать со строками
    # вне ASCII, а подставить такое значение можно и через подделанный state.
    return secrets.compare_digest(ожидаемое.encode(), полученное.encode())


async def _post_token(http: httpx.AsyncClient, поля: dict[str, str]) -> OAuthToken:
    """Общий обмен на Яндекс OAuth."""
    try:
        ответ = await http.post(TOKEN_URL, data=поля)
    except httpx.HTTPError as ошибка:
        raise YandexOAuthError("Яндекс недоступен") from ошибка

    if ответ.status_code >= 400:
        # Текст ошибки не разбираем: у Яндекса он машинный, а в журнале нам
        # нужны наши слова. Секрет приложения в сообщение не попадает.
        код = ответ.json().get("error", "") if ответ.content else ""
        if код == ОТКАЗ_ПОЛЬЗОВАТЕЛЯ:
            raise YandexAuthDeniedError("Человек отказал приложению в доступе")
        raise YandexOAuthError("Яндекс отклонил запрос")

    return _в_токен(ответ.json())


def _в_токен(данные: dict[str, object]) -> OAuthToken:
    """Разбор ответа Яндекса в наш токен."""
    return OAuthToken(
        access_token=str(данные.get("access_token", "")),
        refresh_token=str(данные.get("refresh_token", "")),
        expires_at=datetime.now(UTC)
        + timedelta(seconds=int(данные.get("expires_in", 0)) - ЗАПАС_ПЕРЕД_ИСТЕЧЕНИЕМ_СЕКУНД),
        scope=str(данные.get("scope", "")),
    )


async def exchange_code(
    http: httpx.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
) -> OAuthToken:
    """Код подтверждения в обмен на токен."""
    return await _post_token(
        http,
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
    )


async def refresh_access_token(
    http: httpx.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> OAuthToken:
    """Продлить токен. Отказ здесь означает, что надо входить заново."""
    return await _post_token(
        http,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )


async def fetch_profile(http: httpx.AsyncClient, *, access_token: str) -> dict[str, object]:
    """Профиль эксперта по токену.

    Токен уходит в заголовке, а не параметром адреса: в параметре он попал бы в
    журнал веб-сервера и в историю браузера.
    """
    try:
        ответ = await http.get(
            USERINFO_URL,
            params={"format": "json"},
            headers={"Authorization": f"OAuth {access_token}"},
        )
    except httpx.HTTPError as ошибка:
        raise YandexOAuthError("Яндекс недоступен") from ошибка

    if ответ.status_code >= 400:
        raise YandexOAuthError("Яндекс не отдал профиль")

    return ответ.json()  # type: ignore[no-any-return]


async def get_http_client() -> AsyncGenerator[httpx.AsyncClient]:
    """Клиент httpx на время запроса.

    Отдельная зависимость, а не клиент внутри модуля: так тесты подменяют
    транспорт, не трогая этот код.
    """
    async with httpx.AsyncClient(timeout=ТАЙМАУТ_СЕКУНД) as клиент:
        yield клиент