"""Таблицы, относящиеся к входу эксперта.

Таблицы встреч, заявок и сеансов описаны в онтологии, но появятся вместе с
соответствующими этапами, а не заранее.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.crypto import EncryptedText
from app.db import Base

# Роли эксперта. Роль не назначается из интерфейса: она выдаётся при первом входе
# и меняется вручную администратором.
РОЛЬ_ОРГАНИЗАТОР = "organizer"
РОЛЬ_АДМИНИСТРАТОР = "admin"


def _сейчас() -> datetime:
    return datetime.now(UTC)


class User(Base):
    """Эксперт, записавшийся через Яндекс ID."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    yandex_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    login: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default=РОЛЬ_ОРГАНИЗАТОР
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_сейчас
    )

    sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"User(id={self.id}, login={self.login!r}, role={self.role!r})"


class OAuthToken(Base):
    """Токен Яндекс ID: создавать встречи от имени эксперта.

    Значения шифруются, потому что токен даёт доступ к аккаунту человека.
    Один токен на эксперта, новый вход заменяет старый.
    """

    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    access_token: Mapped[str] = mapped_column(
        EncryptedText("PII_ENCRYPTION_KEY"), nullable=False
    )
    refresh_token: Mapped[str] = mapped_column(
        EncryptedText("PII_ENCRYPTION_KEY"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scope: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_сейчас
    )

    user: Mapped[User] = relationship()


class AuthSession(Base):
    """Пропуск эксперта в панель.

    В куке лежит токен, здесь только его HMAC. Поэтому утечка дампа не даёт
    возможности войти, а выход работает удалением строки, а не ожиданием
    истечения срока.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_сейчас
    )

    user: Mapped[User] = relationship(back_populates="sessions")

    def __repr__(self) -> str:
        return f"AuthSession(id={self.id}, user_id={self.user_id})"


# Индекс на пару «токен и срок» не нужен: поиск идёт по `token_hash`, он и так
# уникален. Экономим место на индексах таблицы, которая читается на каждом
# обращении к панели.
Index("ix_auth_sessions_expires_at", AuthSession.expires_at)