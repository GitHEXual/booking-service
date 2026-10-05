"""Разбор профиля Яндекс ID.

Ответ `/info` содержит больше полей, чем нужно, часть полей может отсутствовать,
а почта может быть не в `default_email`. Задача модуля взять ровно те данные, на
которых строится учётная запись, и отказаться, если данных не хватает.

Поля вроде даты рождения и телефона нам не нужны. Если они попадут в учётную
запись, их придётся шифровать и хранить без причины, поэтому в
`YandexProfile` их нет вовсе.
"""

from dataclasses import dataclass


class ProfileError(Exception):
    """Ответ Яндекса не годится для создания учётной записи."""


@dataclass(frozen=True, slots=True)
class YandexProfile:
    """Данные эксперта в виде, в котором мы их храним."""

    yandex_id: str
    login: str
    email: str
    display_name: str


def _text(значение: object) -> str:
    """Строка из ответа Яндекса, обрезанная от пробелов."""
    if not isinstance(значение, str):
        return ""
    return значение.strip()


def _email_из(ответ: dict[str, object]) -> str:
    """Почта из `default_email`, иначе из первого в списке `emails`."""
    почта = _text(ответ.get("default_email")).lower()
    if почта:
        return почта

    список = ответ.get("emails")
    if isinstance(список, list):
        for кандидат in список:
            почта = _text(кандидат).lower()
            if почта:
                return почта

    return ""


def parse_profile(ответ: dict[str, object]) -> YandexProfile:
    """Учётные данные эксперта из ответа `/info`.

    Тексты ошибок намеренно не содержат данных профиля: они попадут в журнал,
    а профиль содержит почту.
    """
    yandex_id = _text(ответ.get("id"))
    login = _text(ответ.get("login")).lower()

    if not yandex_id:
        raise ProfileError("В ответе Яндекса нет идентификатора пользователя")
    if not login:
        raise ProfileError("В ответе Яндекса нет логина")

    email = _email_из(ответ)
    if not email and "@" not in login:
        # Домен не угадываем. Логин вроде `ivan` может принадлежать ящику на
        # любом домене, и `ivan@yandex.ru` может оказаться чужим. Лучше отказать
        # во входе, чем отправить эксперту письмо не туда.
        raise ProfileError("В ответе Яндекса нет адреса почты")

    display_name = _text(ответ.get("real_name")) or _text(ответ.get("display_name"))

    return YandexProfile(
        yandex_id=yandex_id,
        login=login,
        email=email or login,
        display_name=display_name or login,
    )