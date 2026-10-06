"""Вход и выход эксперта через OAuth Яндекс ID.

Вход состоит из двух запросов, как и положено OAuth:

1. `GET /auth/yandex` отправляет человека на Яндекс. Перед уходом в куку
   кладётся подписанный `state` и `code_verifier`.
2. `GET /auth/yandex/callback` принимает код от Яндекса, обменивает его на токен
   у себя на сервере, читает профиль и выдаёт куку сессии.

Яндекс после шага 2 больше не участвует: сессия наша, и токен Яндекса в браузере
не появляется.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import Settings, get_settings
from backend.cookies import (
    СРОК_КУКИ_АВТОРИЗАЦИИ_СЕКУНД,
    временная_кука_авторизации,
    кука_сессии,
    пустая_кука_сессии,
)
from backend.db import get_session
from backend.models import AuthSession, OAuthToken, User
from backend.session_tokens import (
    SESSION_TTL_DAYS,
    generate_token,
    hash_token,
    is_session_valid,
    new_session_expiry,
)
from backend.yandex.client import (
    НЕТ_ТАКИХ_ПРАВ,
    YandexAuthDeniedError,
    YandexOAuthError,
    build_authorize_url,
    exchange_code,
    fetch_profile,
    get_http_client,
    new_code_verifier,
    new_state,
    verify_state,
)
from backend.yandex.client import OAuthToken as ТокенЯндекса
from backend.yandex.profile import ProfileError, YandexProfile, parse_profile

router = APIRouter(prefix="/auth", tags=["вход"])

# Куда отправляем после входа. Отдельная страница профиля, а не корень: на этом
# этапе у эксперта ещё нет ни расписания, ни видов встреч.
ПОСЛЕ_ВХОДА = "/profile"


def адрес_после_входа(настройки: Settings) -> str:
    """Полный адрес страницы, на которую возвращаем после входа.

    Яндекс возвращает человека на адрес сервиса, а страница профиля живёт на
    интерфейсе. В разработке это разные порты, поэтому адрес собирается из
    `APP_BASE_URL`: иначе редирект увёл бы на сервис, где страницы нет.

    Хвост адреса отбрасывается, чтобы настройка работала и с портом, и без
    него: `http://localhost:5173` и `http://localhost:5173/` дают одно и то же.
    """
    return f"{настройки.app_base_url.rstrip('/')}{ПОСЛЕ_ВХОДА}"


async def require_expert(
    session: str | None = Cookie(default=None),
    сессия: AsyncSession = Depends(get_session),
) -> User:
    """Эксперт по куке сессии.

    Бросает 401, если куки нет, сессия не найдена, истекла или удалена на
    сервере. Удаление строки отменяет вход немедленно, не дожидаясь срока.
    """
    if not session:
        raise HTTPException(401, "Нужно войти")

    запись = (
        await сессия.execute(
            select(AuthSession).where(AuthSession.token_hash == hash_token(session))
        )
    ).scalar_one_or_none()

    if запись is None or not is_session_valid(запись.expires_at):
        raise HTTPException(401, "Сессия закончилась, войдите заново")

    эксперт = (
        await сессия.execute(select(User).where(User.id == запись.user_id))
    ).scalar_one_or_none()

    if эксперт is None:
        raise HTTPException(401, "Сессия закончилась, войдите заново")

    return эксперт


@dataclass(frozen=True, slots=True)
class ВременныеДанные:
    """`state` и `code_verifier` шага авторизации."""

    state: str
    code_verifier: str


def _подписант(настройки: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(настройки.session_secret, salt="yandex-auth")


def положить_куку(response: Response, кука: dict[str, str]) -> None:
    """Выставить куку с перечисленными свойствами."""
    response.set_cookie(**кука, secure=response.headers.get("x-forwarded-proto") == "https")


@router.get("/yandex")
async def начать_вход(response: Response, настройки: Settings = Depends(get_settings)) -> Response:
    """Отправить эксперта на Яндекс за разрешением."""
    state = new_state()
    верификатор = new_code_verifier()

    положить_куку(
        response,
        временная_кука_авторизации(
            _подписант(настройки).dumps({"s": state, "v": верификатор})
        ),
    )

    адрес = build_authorize_url(
        state=state,
        code_verifier=верификатор,
        client_id=настройки.yandex_client_id,
        redirect_uri=настройки.yandex_redirect_uri,
        optional_scope=настройки.yandex_optional_scope,
    )
    response.headers["location"] = адрес
    response.status_code = 307
    return response


def _прочитать_временные(настройки: Settings, кука: str | None) -> ВременныеДанные:
    """Достать `state` и верификатор из подписанной куки."""
    if not кука:
        raise HTTPException(403, "Сессия входа не найдена, начните вход заново")
    try:
        данные = _подписант(настройки).loads(кука, max_age=СРОК_КУКИ_АВТОРИЗАЦИИ_СЕКУНД)
    except (BadSignature, SignatureExpired) as ошибка:
        raise HTTPException(403, "Сессия входа устарела, начните вход заново") from ошибка
    return ВременныеДанные(state=данные["s"], code_verifier=данные["v"])


def _ошибка_от_яндекса(error: str) -> HTTPException:
    """Что ответить на отказ Яндекса.

    Текст выбираем по коду, а не один на все случаи. Иначе отказ человека и
    неверная настройка приложения выглядят одинаково, и человек зря ищет у
    себя проблему, которой нет.
    """
    if error == НЕТ_ТАКИХ_ПРАВ:
        return HTTPException(
            500,
            "У приложения нет запрошенных прав доступа. "
            "Их нужно добавить в настройках приложения Яндекс OAuth.",
        )
    return HTTPException(400, "Вы не разрешили приложению доступ. Попробуйте ещё раз.")


async def _профиль_по_коду(
    http: httpx.AsyncClient, настройки: Settings, *, код: str, верификатор: str
) -> tuple[YandexProfile, ТокенЯндекса]:
    """Обменять код на токен и прочитать профиль.

    Ошибки Яндекса переводятся в ответы сервиса: отказ человека это 400, сбой на
    стороне Яндекса это 502. Второе важно различать, потому что 400 читается
    как «человек что-то сделал не так» и чинить будут его.
    """
    try:
        токен = await exchange_code(
            http,
            client_id=настройки.yandex_client_id,
            client_secret=настройки.yandex_client_secret,
            redirect_uri=настройки.yandex_redirect_uri,
            code=код,
            code_verifier=верификатор,
        )
        ответ_яндекса = await fetch_profile(http, access_token=токен.access_token)
    except YandexAuthDeniedError as ошибка:
        raise HTTPException(400, str(ошибка)) from ошибка
    except YandexOAuthError as ошибка:
        raise HTTPException(502, str(ошибка)) from ошибка

    try:
        return parse_profile(ответ_яндекса), токен
    except ProfileError as ошибка:
        raise HTTPException(502, str(ошибка)) from ошибка


@router.get("/yandex/callback")
async def завершить_вход(
    response: Response,
    code: str | None = None,
    error: str | None = None,
    state: str | None = None,
    yandex_auth: str | None = Cookie(default=None),
    сессия: AsyncSession = Depends(get_session),
    настройки: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> Response:
    """Принять код от Яндекса и выдать сессию."""
    if error:
        raise _ошибка_от_яндекса(error)

    if not code:
        raise HTTPException(400, "Яндекс не вернул код подтверждения")

    временные = _прочитать_временные(настройки, yandex_auth)

    # Порядок именно такой: сначала state, потом обмен кода. Подделанный код
    # не должен обращаться к Яндексу, пока мы не убедились, что он наш.
    if not verify_state(временные.state, state):
        raise HTTPException(403, "Ответ авторизации не совпадает с запросом")

    профиль, токен = await _профиль_по_коду(
        http,
        настройки,
        код=code,
        верификатор=временные.code_verifier,
    )
    эксперт = await _записать_эксперта(сессия, профиль, токен)
    await сессия.flush()

    # Прежние сессии не трогаем: у эксперта может быть несколько входов,
    # например с телефона и с компьютера, см. `docs/ontology.md`. Выход
    # удаляет только свою сессию, поэтому старые устройства работают дальше.
    token = generate_token()
    сессия.add(
        AuthSession(
            user_id=эксперт.id,
            token_hash=hash_token(token),
            expires_at=new_session_expiry(),
        )
    )

    положить_куку(response, кука_сессии(token, SESSION_TTL_DAYS * 86400))
    # Временная кука больше не нужна и содержит верификатор, который
    # второй раз применить нельзя.
    положить_куку(response, временная_кука_авторизации(""))

    response.headers["location"] = адрес_после_входа(настройки)
    response.status_code = 307
    return response


async def _записать_эксперта(
    сессия: AsyncSession, профиль: YandexProfile, токен: ТокенЯндекса
) -> User:
    """Найти или создать эксперта и обновить его токен."""
    эксперт = (
        await сессия.execute(select(User).where(User.yandex_id == профиль.yandex_id))
    ).scalar_one_or_none()

    if эксперт is None:
        эксперт = User(
            yandex_id=профиль.yandex_id,
            login=профиль.login,
            email=профиль.email,
            display_name=профиль.display_name,
        )
        сессия.add(эксперт)
        await сессия.flush()

    эксперт.last_login_at = datetime.now(UTC)

    сохранённый = (
        await сессия.execute(
            select(OAuthToken).where(OAuthToken.user_id == эксперт.id)
        )
    ).scalar_one_or_none()
    if сохранённый is None:
        сессия.add(
            OAuthToken(
                user_id=эксперт.id,
                access_token=токен.access_token,
                refresh_token=токен.refresh_token,
                expires_at=токен.expires_at,
                scope=токен.scope,
            )
        )
    else:
        # Токен один на эксперта, новый выданный заменяет старый: хранить оба
        # незачем, из двух непонятно какой брать.
        сохранённый.access_token = токен.access_token
        сохранённый.refresh_token = токен.refresh_token
        сохранённый.expires_at = токен.expires_at
        сохранённый.scope = токен.scope
        сохранённый.updated_at = datetime.now(UTC)

    return эксперт


@router.post("/logout")
async def выйти(
    response: Response,
    session: str | None = Cookie(default=None),
    сессия: AsyncSession = Depends(get_session),
) -> Response:
    """Отозвать сессию.

    Статус задаётся явно. FastAPI создаёт внедрённый `Response` с пустым
    статусом и оставляет его таким, если обработчик статус не выставил: пустой
    статус нельзя отдать по протоколу, и запрос падает уже при ответе.
    """
    if session:
        await сессия.execute(
            delete(AuthSession).where(AuthSession.token_hash == hash_token(session))
        )
    положить_куку(response, пустая_кука_сессии())
    response.status_code = 204
    return response


@router.get("/me")
async def кто_я(
    эксперт: User = Depends(require_expert),
) -> dict[str, object]:
    """Кто вошёл. Требует действующей сессии."""
    return {
        "id": эксперт.id,
        "login": эксперт.login,
        "email": эксперт.email,
        "display_name": эксперт.display_name,
        "role": эксперт.role,
    }