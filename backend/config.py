"""Настройки приложения из переменных окружения и файла `.env`."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Имена полей совпадают с именами переменных окружения с точностью до
    # регистра: Pydantic сам сопоставляет `session_secret` и `SESSION_SECRET`.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_base_url: str = "http://localhost:8000"

    database_url: str
    test_database_url: str | None = None

    # Ключ подписи и хеширования токенов сессии. Обязателен: без него
    # приложение не запускается, и это лучше, чем стартовать с пустым ключом.
    session_secret: str = Field(min_length=32)

    pii_encryption_key: str
    pii_lookup_key: str

    yandex_client_id: str
    yandex_client_secret: str
    yandex_redirect_uri: str

    # Права Телемоста. Пусто, пока их нет у приложения: право, которого нет в
    # настройках приложения, отменяет всю авторизацию, а не только выдачу
    # токена. Вписать сюда `telemost-api:conferences.create` и остальные можно
    # после того, как они появятся на странице приложения в кабинете OAuth.
    yandex_optional_scope: str = ""

    smtp_host: str = "smtp.yandex.ru"
    smtp_port: int = 465
    smtp_username: str
    smtp_password: str

    consent_document_version: str


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]