"""Тесты токенов сессии.

Токен это секрет, который лежит в куке браузера, а в базе хранится только его
хеш. Отсюда требования: токен должен быть непредсказуемым, хеш не должен
позволять восстановить токен, а сравнение хешей не должно зависеть от времени
выполнения.
"""

from datetime import UTC, datetime, timedelta

from backend.session_tokens import (
    SESSION_TTL_DAYS,
    generate_token,
    hash_token,
    is_session_valid,
    new_session_expiry,
)

# Длина токена в байтах. 32 байта это 256 бит энтропии, подбор бессмысленен.
TOKEN_BYTES = 32


class TestGenerateToken:
    def test_токен_содержит_32_байта_энтропии(self):
        assert len(generate_token().encode()) >= 43

    def test_два_вызова_дают_разные_токены(self):
        assert generate_token() != generate_token()

    def test_токен_без_пробелов_и_двоеточий(self):
        # Значение попадает в заголовок Set-Cookie и в куку браузера,
        # поэтому пробел и точка с запятой в нём недопустимы.
        токен = generate_token()
        assert not set(токен) & set(" ;,=")


class TestHashToken:
    def test_хеш_детерминирован(self):
        # Один и тот же токен обязан находиться в базе по одному и тому же хешу,
        # иначе повторный запрос перестал бы находить свою сессию.
        assert hash_token("abc") == hash_token("abc")

    def test_хеш_не_равен_токену(self):
        assert hash_token("abc") != "abc"

    def test_разные_токены_дают_разные_хеши(self):
        assert hash_token("abc") != hash_token("abd")

    def test_хеш_одинаковой_длины(self):
        # Длина хеша не должна зависеть от длины токена, иначе по размеру
        # значения в базе можно отличить токен от мусора.
        assert len(hash_token("a")) == len(hash_token("a" * 500))

    def test_хеш_не_читается_обратно(self):
        # Токен из 32 байт не должен восстанавливаться из хеша подбором.
        токен = generate_token()
        assert токен not in hash_token(токен)


class TestSessionValidity:
    def test_сессия_только_что_создана_действительна(self):
        assert is_session_valid(new_session_expiry())

    def test_срок_равен_семи_дням(self):
        assert SESSION_TTL_DAYS == 7

    def test_срок_отсчитывается_от_сейчас(self):
        assert new_session_expiry() > datetime.now(UTC) + timedelta(days=6)

    def test_на_границе_истечения_уже_недействительна(self):
        # Секунда до истечения действует, на самой границе уже нет. Проверяем
        # подставленным временем, иначе тест зависит от того, как быстро он
        # выполняется.
        граница = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
        assert is_session_valid(граница, now=граница - timedelta(seconds=1))
        assert not is_session_valid(граница, now=граница)

    def test_просроченная_сессия_недействительна(self):
        просрочена = datetime.now(UTC) - timedelta(seconds=1)
        assert not is_session_valid(просрочена)