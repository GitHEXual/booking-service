"""Создание видеовстречи в Яндекс Телемосте.

Токен берётся тот же, что и для входа: одно приложение Яндекс OAuth
закрывает и вход, и создание встреч, см. `docs/adr/0007`. Отдельного
приложения и ручной выдачи токенов не нужно.

Справка: `https://yandex.ru/dev/telemost/doc/ru/conferences/conference-create.md`
"""

from dataclasses import dataclass

import httpx

СОЗДАТЬ_ВСТРЕЧУ = "https://cloud-api.yandex.net/v1/telemost-api/conferences"

# Яндекс отвечает медленно при первом обращении, но и долго не отвечает никогда.
# Десяти секунд хватает: дальше всё равно пора показать человеку ошибку.
ТАЙМАУТ_СЕКУНД = 10.0

# Право на создание встречи. Приложение без него получает 403.
НУЖНОЕ_ПРАВО = "telemost-api:conferences.create"


class TelemostError(Exception):
    """Встречу создать не удалось."""


@dataclass(frozen=True, slots=True)
class Встреча:
    """Созданная встреча."""

    conference_id: str
    join_url: str


async def создать_встречу(
    http: httpx.AsyncClient, *, access_token: str
) -> Встреча:
    """Создать публичную видеовстречу и вернуть ссылку для участников.

    Комната ожидания выключена (`PUBLIC`): гость не должен ждать, пока
    эксперт нажмёт «впустить». Ссылка и так не попадает никому, кроме тех,
    кому мы отправили письмо.
    """
    ответ = await _запрос(
        http,
        access_token=access_token,
        тело={"waiting_room_level": "PUBLIC"},
    )

    conference_id = ответ.get("id")
    join_url = ответ.get("join_url")
    if not conference_id or not join_url:
        raise TelemostError("Телемост не вернул ссылку на встречу")

    return Встреча(conference_id=str(conference_id), join_url=str(join_url))


async def _запрос(
    http: httpx.AsyncClient, *, access_token: str, тело: dict[str, object]
) -> dict:
    """Отправить запрос к Телемосту и разобрать ответ."""
    try:
        ответ = await http.post(
            СОЗДАТЬ_ВСТРЕЧУ,
            json=тело,
            # Имя схемы именно `OAuth`: в документации Яндекса оно такое,
            # хотя в ответе на обмен токена поле `token_type` содержит `bearer`.
            headers={"Authorization": f"OAuth {access_token}"},
            timeout=ТАЙМАУТ_СЕКУНД,
        )
    except httpx.HTTPError as ошибка:
        raise TelemostError("Телемост недоступен") from ошибка

    if ответ.status_code == 201:
        return ответ.json()

    raise TelemostError(_причина_отказа(ответ))


def _причина_отказа(ответ: httpx.Response) -> str:
    """Что ответил Телемост, своими словами.

    Текст из ответа не показываем целиком: он машинный и может содержать
    детали запроса. Человеку нужна причина, а не протокол.
    """
    if ответ.status_code in (401, 403):
        return (
            "Нет доступа к Телемосту. Проверьте, что у приложения есть право "
            f"{НУЖНОЕ_ПРАВО} и что эксперт вошёл заново после его выдачи."
        )
    if ответ.status_code == 429:
        return "Телемост временно не отвечает, попробуйте позже"
    return f"Телемост ответил кодом {ответ.status_code}"
