"""Токены сессий эксперта.

Схема такая: в куке браузера лежит токен, в базе только его хеш. Поэтому утечка
дама базы не даёт возможности войти, а выход работает удалением строки, а не
ожиданием истечения срока.

Хеш считается не на сам токен, а на HMAC от него под ключом приложения. Это
закрывает случай, когда кто-то положит в базу заранее посчитанный хеш
произвольного токена и войдёт под ним: без ключа такой хеш не подобрать.
"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from backend.config import get_settings

# Сколько живёт сессия без продления.
SESSION_TTL_DAYS = 7

# 32 байта это 256 бит энтропии, подбор бессмысленен.
TOKEN_BYTES = 32


def generate_token() -> str:
    """Новый случайный токен сессии.

    Значение безопасно класть в куку: в нём нет символов, которые в заголовке
    Set-Cookie имеют служебный смысл.
    """
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Хеш токена для хранения в базе."""
    ключ = get_settings().session_secret.encode()
    return hmac.new(ключ, token.encode(), hashlib.sha256).hexdigest()


def tokens_equal(left: str, right: str) -> bool:
    """Сравнение хешей за постоянное время."""
    return hmac.compare_digest(left, right)


def new_session_expiry() -> datetime:
    """Момент, когда сессия истекает."""
    return datetime.now(UTC) + timedelta(days=SESSION_TTL_DAYS)


def is_session_valid(expires_at: datetime, now: datetime | None = None) -> bool:
    """Действует ли сессия.

    Время внедряется параметром, чтобы границу истечения можно было проверить
    тестом, а не наблюдением за скоростью его выполнения.
    """
    момент = now or datetime.now(UTC)
    return expires_at > момент