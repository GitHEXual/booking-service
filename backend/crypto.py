"""Шифрование значений в колонках базы.

Тип `EncryptedText` прячет значение от того, кто получил файл базы или её
копию: без ключа из окружения столбец не читается.

Ключ берётся из окружения и в базе не хранится. Поэтому служебные данные,
которые потерять нельзя, в такую колонку не кладутся: пароль от сессии хранится
не шифрованием, а хешем.
"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import LargeBinary
from sqlalchemy.types import TypeDecorator

# Случайное число, приписываемое к шифротексту. Оно нужно, чтобы две одинаковые
# почты не давали одинаковые байты в базе.
_ДЛИНА_СЛУЧАЙНЫХ = 12


def _key_из_окружения(имя: str) -> bytes:
    значение = os.environ.get(имя, "")
    if not значение:
        raise RuntimeError(f"Не задана переменная окружения {имя}")
    ключ = base64.urlsafe_b64decode(значение + "=" * (-len(значение) % 4))
    if len(ключ) != 32:
        raise RuntimeError(f"В {имя} ожидалось 32 байта, получено {len(ключ)}")
    return ключ


class EncryptedText(TypeDecorator[str]):
    """Строка, в базе лежащая зашифрованной.

    В коде остаётся обычной строкой: `EncryptedText` только меняет то, что
    уходит в базу и что приходит из неё.
    """

    impl = LargeBinary

    #: Принимает аргумент, поэтому кэшировать нельзя: ключа у экземпляра
    #: нет, а состояние у него есть.
    cache_ok = False

    #: Значение по умолчанию нужно автогенерации Alembic: она восстанавливает
    #: тип в миграции как `EncryptedText()`, без аргументов.
    def __init__(self, переменная_окружения: str = "PII_ENCRYPTION_KEY") -> None:
        super().__init__()
        self.переменная_окружения = переменная_окружения

    def process_bind_param(self, значение: str | None, диалект) -> bytes | None:
        if значение is None:
            return None
        случайные = os.urandom(_ДЛИНА_СЛУЧАЙНЫХ)
        шифротекст = AESGCM(_key_из_окружения(self.переменная_окружения)).encrypt(
            случайные,
            значение.encode(),
            None,
        )
        return случайные + шифротекст

    def process_result_value(self, значение: bytes | None, диалект) -> str | None:
        if значение is None:
            return None
        данные = bytes(значение)
        return AESGCM(_key_из_окружения(self.переменная_окружения)).decrypt(
            данные[:_ДЛИНА_СЛУЧАЙНЫХ],
            данные[_ДЛИНА_СЛУЧАЙНЫХ:],
            None,
        ).decode()