"""Тесты разбора профиля Яндекс ID.

Ответ `/info` содержит больше полей, чем нам нужно, часть из них может
отсутствовать, а часть нужна не в том виде, в каком приходит. Задача модуля
взять из ответа ровно те данные, на которых строится учётная запись, и
отказаться, если данных недостаточно.
"""

import pytest

from backend.yandex.profile import ProfileError, parse_profile

# Ответ `/info` в том виде, в каком его возвращает Яндекс. Поля, которые мы
# не используем, оставлены: они должны игнорироваться, а не мешать.
ОТВЕТ_ЯНДЕКСА = {
    "id": "1000034427",
    "login": "ShkutanMaxaon",
    "client_id": "a1b2c3d4e5f6",
    "display_name": "Максим",
    "real_name": "Шкутан Максимович",
    "first_name": "Максим",
    "last_name": "Шкутан",
    "sex": "male",
    "default_email": "shkutanmaxaon@yandex.ru",
    "emails": ["shkutanmaxaon@yandex.ru", "shkutanmaxaon@ya.ru"],
    "psuid": "1a2b3c4d5e6f",
    "birthday": "1990-01-01",
    "default_phone": {"id": "1", "number": "+70000000000"},
}


class TestParseProfile:
    def test_идентификатор_берётся_из_id(self):
        профиль = parse_profile(ОТВЕТ_ЯНДЕКСА)
        assert профиль.yandex_id == "1000034427"

    def test_логин_берётся_из_login(self):
        ответ = {**ОТВЕТ_ЯНДЕКСА, "login": "shkutanmaxaon"}
        assert parse_profile(ответ).login == "shkutanmaxaon"

    def test_почта_берётся_из_default_email(self):
        assert parse_profile(ОТВЕТ_ЯНДЕКСА).email == "shkutanmaxaon@yandex.ru"

    def test_имя_берётся_из_real_name(self):
        assert parse_profile(ОТВЕТ_ЯНДЕКСА).display_name == "Шкутан Максимович"

    def test_лишние_поля_игнорируются(self):
        # Поля вроде даты рождения и телефона нам не нужны. Если они попадут
        # в учётную запись, их придётся шифровать и хранить без причины.
        профиль = parse_profile(ОТВЕТ_ЯНДЕКСА)
        assert not hasattr(профиль, "birthday")
        assert not hasattr(профиль, "default_phone")

    def test_логин_приводится_к_нижнему_регистру(self):
        # Яндекс не считает регистр значимым в логине, но вернуть его может.
        # Если хранить как есть, один и тот же человек получит два аккаунта.
        профиль = parse_profile({**ОТВЕТ_ЯНДЕКСА, "login": "ShkutanMaxaon"})
        assert профиль.login == "shkutanmaxaon"

    def test_почта_приводится_к_нижнему_регистру(self):
        профиль = parse_profile(
            {**ОТВЕТ_ЯНДЕКСА, "default_email": "ShkutanMaxaon@Yandex.RU"}
        )
        assert профиль.email == "shkutanmaxaon@yandex.ru"

    def test_почта_обрезается_от_пробелов(self):
        профиль = parse_profile({**ОТВЕТ_ЯНДЕКСА, "default_email": "  a@yandex.ru  "})
        assert профиль.email == "a@yandex.ru"


class TestFallbacks:
    def test_почта_берётся_из_emails_если_нет_default(self):
        # У аккаунта может не быть основной почты, но быть список.
        ответ = {**ОТВЕТ_ЯНДЕКСА}
        del ответ["default_email"]
        assert parse_profile(ответ).email == "shkutanmaxaon@yandex.ru"

    def test_имя_подставляется_из_display_name(self):
        # У аккаунта на домене `real_name` может не быть, а `display_name`
        # заполнено именем, которое человек сам выбрал.
        ответ = {**ОТВЕТ_ЯНДЕКСА}
        del ответ["real_name"]
        assert parse_profile(ответ).display_name == "Максим"

    def test_имя_подставляется_из_логина_если_нет_имён(self):
        # Пустое имя в панели выглядит поломанным, логин лучше пустоты.
        ответ = {**ОТВЕТ_ЯНДЕКСА}
        del ответ["real_name"]
        del ответ["display_name"]
        assert parse_profile(ответ).display_name == "shkutanmaxaon"


class TestEmailFallback:
    def test_почта_берётся_из_логина_если_логин_сам_адрес(self):
        # У аккаунта на домене организации логин сам является адресом.
        ответ = {**ОТВЕТ_ЯНДЕКСА}
        del ответ["default_email"]
        del ответ["emails"]
        ответ["login"] = "ivanov@sfu.ru"
        assert parse_profile(ответ).email == "ivanov@sfu.ru"

    def test_почта_не_придумывается_из_логина(self):
        # Домен не угадывается. Если Яндекс не вернул почту, а логин похож на
        # имя без домена, то `логин@yandex.ru` может оказаться чужим ящиком, и
        # мы отправим эксперту письмо не туда. Лучше отказать во входе.
        ответ = {**ОТВЕТ_ЯНДЕКСА}
        del ответ["default_email"]
        del ответ["emails"]
        with pytest.raises(ProfileError):
            parse_profile({**ответ, "login": "shkutanmaxaon"})


class TestRejectsIncompleteProfile:
    def test_без_id_отклоняется(self):
        ответ = {**ОТВЕТ_ЯНДЕКСА}
        del ответ["id"]
        with pytest.raises(ProfileError):
            parse_profile(ответ)

    def test_без_login_отклоняется(self):
        # Без логина не найти ни существующего пользователя, ни построить
        # почту. Запись без логина повесит идентификацию эксперта.
        ответ = {**ОТВЕТ_ЯНДЕКСА}
        del ответ["login"]
        with pytest.raises(ProfileError):
            parse_profile(ответ)

    def test_пустой_id_отклоняется(self):
        with pytest.raises(ProfileError):
            parse_profile({**ОТВЕТ_ЯНДЕКСА, "id": ""})

    def test_пустой_логин_отклоняется(self):
        with pytest.raises(ProfileError):
            parse_profile({**ОТВЕТ_ЯНДЕКСА, "login": "   "})

    def test_ошибка_не_раскрывает_данные_профиля(self):
        # Текст ошибки попадёт в журнал, а профиль содержит почту. Утекать ему
        # в текст ошибки нельзя.
        ответ = {**ОТВЕТ_ЯНДЕКСА}
        del ответ["id"]
        with pytest.raises(ProfileError) as ошибка:
            parse_profile(ответ)
        assert "yandex.ru" not in str(ошибка.value)