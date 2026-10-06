"""Таблицы сервиса.

Вход эксперта, расписание, виды встреч, сеансы и заявки. Описание предметной
области целиком лежит в `docs/ontology.md`.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.crypto import EncryptedText
from backend.db import Base

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
    # Часовой пояс IANA, например `Asia/Krasnoyarsk`. Расписание хранит локальное
    # время рабочего окна, поэтому без пояса сетку слотов не собрать.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
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


class Schedule(Base):
    """Часы приёма эксперта.

    Принадлежит эксперту, а не виду встречи: часы заводятся один раз и
    используются всеми его видами встреч.
    """

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Дни недели одной строкой, например `1,2,3,4,5`. Наружу отдаётся списком.
    weekdays: Mapped[str] = mapped_column(String(20), nullable=False)
    # Локальное время рабочего окна в часовом поясе владельца.
    start_time: Mapped[str] = mapped_column(String(8), nullable=False)
    end_time: Mapped[str] = mapped_column(String(8), nullable=False)

    owner: Mapped[User] = relationship()

    def __repr__(self) -> str:
        return f"Schedule(id={self.id}, owner_id={self.owner_id})"


class EventType(Base):
    """Вид встречи, то, на что записывается гость."""

    __tablename__ = "event_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    schedule_id: Mapped[int] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Часть публичной ссылки. Уникален в паре с владельцем, чтобы у разных
    # экспертов могли быть одинаковые адреса вида `/u/ivan/konsultaciya`.
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))

    duration_minutes: Mapped[int] = mapped_column(nullable=False)
    time_increment_minutes: Mapped[int] = mapped_column(nullable=False, default=30)
    # За сколько часов минимум можно записаться и на сколько дней вперёд открыта
    # запись. Оба ограничения проверяются при расчёте сетки.
    min_notice_hours: Mapped[int] = mapped_column(nullable=False, default=0)
    horizon_days: Mapped[int] = mapped_column(nullable=False, default=30)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_сейчас
    )

    owner: Mapped[User] = relationship()
    schedule: Mapped[Schedule] = relationship()

    def __repr__(self) -> str:
        return f"EventType(id={self.id}, slug={self.slug!r})"


class Session(Base):
    """Запись о конкретном слоте.

    Слот сам в базе не хранится и вычисляется при каждом запросе. Эта строка
    появляется вместе с первой заявкой на слот и живёт до конца.
    """

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type_id: Mapped[int] = mapped_column(
        ForeignKey("event_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Момент начала в UTC. Вместе с датой в поясе эксперта он однозначно
    # определяет слот, поэтому идентификатора у слота и нет.
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Пока пусто, слот открыт для новых гостей. После закрепления состава
    # состав закрыт навсегда.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    bookings: Mapped[list[Booking]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"Session(id={self.id}, start_at={self.start_at})"


class Booking(Base):
    """Заявка конкретного гостя на конкретный слот."""

    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type_id: Mapped[int] = mapped_column(
        ForeignKey("event_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Имя и почта лежат зашифрованными. Открытого значения в таблице нет.
    guest_name: Mapped[str] = mapped_column(
        EncryptedText("PII_ENCRYPTION_KEY"), nullable=False
    )
    guest_email: Mapped[str] = mapped_column(
        EncryptedText("PII_ENCRYPTION_KEY"), nullable=False
    )
    # Слепой индекс почты: по нему ищем заявки, не расшифровывая всё подряд.
    guest_email_bidx: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    guest_timezone: Mapped[str] = mapped_column(String(64), nullable=False)

    # Статусы, занимающие место в слоте. Прочие состояния место не занимают.
    СТАТУСЫ_С_МЕСТОМ = ("pending", "confirmed")
    СТАТУСЫ = ("pending", "confirmed", "removed", "expired", "cancelled")

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # Версия документа о персональных данных и момент согласия. Хранятся
    # открыто: сам факт согласия никого не разоблачает.
    consent_version: Mapped[str] = mapped_column(String(32), nullable=False)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_сейчас
    )

    session: Mapped[Session] = relationship(back_populates="bookings")

    def __repr__(self) -> str:
        return f"Booking(id={self.id}, status={self.status!r})"


# Слаг уникален в паре с владельцем: у разных экспертов адреса вида
# `/u/ivan/konsultaciya` могут совпадать, у одного эксперта два одинаковых адреса
# означали бы две ссылки на одну встречу, и гостю пришлось бы выбирать.
Index("uq_event_type_owner_slug", EventType.owner_id, EventType.slug, unique=True)

# Слот однозначно задаётся тройкой «вид встречи, начало». Без этого индекса
# две одновременные заявки создали бы два сеанса на один слот, и подсчёт
# занятых мест считал бы их разными.
Index(
    "uq_session_event_start",
    Session.event_type_id,
    Session.start_at,
    unique=True,
)

# Один слот это один гость. Правило держит сам индекс, а не код обработчика:
# две одновременные заявки на последнее место не могут обе пройти проверку,
# потому что вторая упрётся в нарушение уникальности.
#
# Индекс частичный: освобождённая заявка (`removed`, `expired`, `cancelled`)
# место не держит, поэтому после отмены это время снова свободно.
Index(
    "uq_booking_active_session",
    Booking.session_id,
    unique=True,
    postgresql_where=Booking.status.in_(("pending", "confirmed")),
)

# Поиск заявок эксперта по времени: список гостей слота и заявки по датам.
Index("ix_bookings_owner_session", Booking.owner_id, Booking.session_id)
