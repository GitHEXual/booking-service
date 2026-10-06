"""Проверка входных данных.

Схемы Pydantic живут отдельно от обработчиков: обработчик знает, что делать с
заявкой, схема знает, что вообще можно прислать. Тексты ошибок на русском,
потому что их видит человек.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

ДЛИТЕЙШИЕ_СЛАГ = 64
ДЛИТЕЙШЕЕ_ИМЯ = 100
ДЛИННЕЙШЕЕ_ОПИСАНИЕ = 500
ДОСТУПНЫЕ_ПРАВА_ТЕЛЕМОСТА = "telemost-api:conferences.create"


class РасписаниеВход(BaseModel):
    """Часы приёма, которые задаёт эксперт."""

    weekdays: list[int] = Field(min_length=1, max_length=7)
    start_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @field_validator("weekdays")
    @classmethod
    def _дни_из_диапазона(cls, дни: list[int]) -> list[int]:
        for день in дни:
            if день not in range(1, 8):
                raise ValueError("День недели должен быть от 1 до 7")
        # Убираем повторы и сортируем: иначе один и тот же день можно прислать
        # дважды, и расписание молча разъедется с тем, что показано в панели.
        return sorted(set(дни))

    @field_validator("end_time")
    @classmethod
    def _окно_не_пустое(cls, конец: str, info) -> str:
        # Инвариант И3: начало строго раньше конца.
        начало = info.data.get("start_time")
        if начало and конец <= начало:
            raise ValueError("Начало рабочего окна должно быть раньше конца")
        return конец


class ВидВстречиВход(BaseModel):
    """То, что эксперт заводит, чтобы гость мог записаться."""

    name: str = Field(min_length=1, max_length=ДЛИТЕЙШЕЕ_ИМЯ)
    slug: str = Field(min_length=1, max_length=ДЛИТЕЙШИЕ_СЛАГ)
    description: str | None = Field(default=None, max_length=ДЛИННЕЙШЕЕ_ОПИСАНИЕ)

    duration_minutes: int = Field(ge=5, le=480)
    time_increment_minutes: int = Field(default=30, ge=5, le=480)
    min_notice_hours: int = Field(default=0, ge=0, le=720)
    horizon_days: int = Field(default=30, ge=1, le=90)

    schedule: РасписаниеВход

    @field_validator("slug")
    @classmethod
    def _slug_из_букв(cls, значение: str) -> str:
        # Слаг попадает в адрес, поэтому пробелы и кириллицу пропускаем:
        # адрес должен быть читаемым и набираться руками без URL-кодирования.
        cleaned = значение.strip().lower()
        if not cleaned.isascii():
            raise ValueError("Часть ссылки должна быть латиницей")
        if not all(c.isalnum() or c == "-" for c in cleaned):
            raise ValueError(
                "Часть ссылки может содержать только латинские буквы, цифры и дефис"
            )
        return cleaned


class ЗаявкаВход(BaseModel):
    """Данные, которые гость оставляет, отправляя заявку."""

    start_at: datetime
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    timezone: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=1000)
    # Согласие на обработку персональных данных обязательно: без него заявку
    # нельзя ни принять, ни хранить.
    consent: bool

    @field_validator("consent")
    @classmethod
    def _согласие_дано(cls, значение: bool) -> bool:
        if not значение:
            raise ValueError("Без согласия на обработку персональных данных нельзя")
        return значение

    @field_validator("email")
    @classmethod
    def _похоже_на_почту(cls, значение: str) -> str:
        почта = значение.strip().lower()
        if "@" not in почта or почта.startswith("@") or почта.endswith("@"):
            raise ValueError("Почта указана неверно")
        return почта

    @field_validator("start_at")
    @classmethod
    def _с_часовым_поясом(cls, значение: datetime) -> datetime:
        # Время приходит от гостя в его поясе, поэтому без смещения его нельзя
        # сравнивать с тем, что лежит в базе.
        if значение.tzinfo is None:
            raise ValueError("Время слота должно содержать часовой пояс")
        return значение.astimezone(UTC)


class СсылкаВход(BaseModel):
    """Ссылка на встречу, вписанная экспертом руками."""

    join_url: str = Field(min_length=8, max_length=512)

    @field_validator("join_url")
    @classmethod
    def _это_адрес(cls, значение: str) -> str:
        адрес = значение.strip()
        if not адрес.startswith(("https://", "http://")):
            raise ValueError("Ссылка должна начинаться с https:// или http://")
        return адрес
