"""Фоновая задача: встречи в Телемосте, письма организатору и напоминания.

Встреча создаётся сразу после первой заявки на слот, а не перед началом: так у
организатора появляется ссылка, которую он видит в письме о заявке. Гость
получает письмо со ссылкой перед началом, когда она ему действительно нужна.

Проходов три, и они независимы: создание встреч, постановка напоминаний и
отправка писем. Сбой отправки не сдвигает создание встреч и наоборот.
"""

import asyncio
import logging
import secrets
import string
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend import mail, telemost
from backend.config import Settings, get_settings
from backend.models import Booking, EventType, OAuthToken, OutboxEvent, Session, User

журнал = logging.getLogger("worker")

# Сколько писем отправляем за один проход. Без предела SMTP на большом
# количестве гостей упрётся в лимит и откажет всё сразу.
ПАЧКА = 50

# Сколько раз пробуем отправить письмо, прежде чем признать неудачным.
# Три попытки: почта может подвиснуть на пару минут.
ПОПЫТОК = 3

# Поводы писем. Организатору сообщаем о заявке вместе с созданной встречей,
# гостю напоминаем перед началом.
ПИСЬМО_О_ЗАЯВКЕ = "booking_created"
ПИСЬМО_О_НАЧАЛЕ = "meeting_soon"


async def подготовить_встречи(сессия: AsyncSession) -> int:
    """Создать встречи для слотов, на которые уже есть заявки.

    Возвращает число обработанных сеансов. Сеансы с готовой ссылкой
    пропускаются: повторный проход не должен перетирать уже созданную встречу.
    """
    сеансы = (
        (
            await сессия.execute(
                select(Session)
                .join(Booking, Booking.session_id == Session.id)
                .where(
                    Booking.status.in_(Booking.СТАТУСЫ_С_МЕСТОМ),
                    Session.confirmed_at.is_(None),
                    Session.cancelled_at.is_(None),
                    Session.join_url.is_(None),
                )
                .distinct()
                .order_by(Session.start_at)
            )
        )
        .scalars()
        .all()
    )

    if not сеансы:
        return 0

    async with httpx.AsyncClient() as http:
        for сеанс in сеансы:
            await _подготовить_сеанс(сессия, http, сеанс)
    await сессия.commit()
    return len(сеансы)


async def _подготовить_сеанс(
    сессия: AsyncSession,
    http: httpx.AsyncClient,
    сеанс: Session,
) -> None:
    """Создать встречу для одного сеанса и сообщить организатору."""
    # Ссылка могла прийти с ручным способом: тогда встречу не пересоздаём.
    if сеанс.join_url:
        return

    заявки = (
        (
            await сессия.execute(
                select(Booking, EventType)
                .join(EventType, EventType.id == Booking.event_type_id)
                .where(
                    Booking.session_id == сеанс.id,
                    Booking.status.in_(Booking.СТАТУСЫ_С_МЕСТОМ),
                )
                .order_by(Booking.id)
            )
        )
        .all()
    )
    if not заявки:
        return

    встреча = await _создать_встречу(сессия, http, сеанс)
    if встреча is None:
        return

    сеанс.conference_id = встреча.conference_id
    сеанс.join_url = встреча.join_url
    сеанс.meeting_password = _новый_пароль()
    сеанс.conference_status = "ready"

    эксперт = await _эксперт(сессия, сеанс)
    if эксперт is None:
        # Владельца нет, письмо слать некуда. Встреча при этом уже создана.
        журнал.warning("у сеанса %s нет владельца", сеанс.id)
        return

    заявка, вид = заявки[0]
    время = _время_в_поясе(сеанс, эксперт.timezone)
    сессия.add(
        OutboxEvent(
            kind=ПИСЬМО_О_ЗАЯВКЕ,
            booking_id=заявка.id,
            to_email=эксперт.email,
            subject=mail.тема_письма_о_заявке(вид.name),
            body=mail.текст_письма_организатору(
                встреча=вид.name,
                гость=заявка.guest_name,
                почта=заявка.guest_email,
                время=время,
                ссылка=встреча.join_url,
            ),
        )
    )

    журнал.info("встреча создана, сеанс %s", сеанс.id)


async def подготовить_напоминания(
    сессия: AsyncSession, настройки: Settings
) -> int:
    """Поставить гостям письма о встрече, до которой осталось несколько минут.

    Возвращает число поставленных писем. Повторно одному гостю письмо не
    ставится: иначе частый проход задачи засыпал бы человека письмами.
    """
    сейчас = datetime.now(UTC)
    начало = сейчас + timedelta(
        minutes=настройки.reminder_lead_minutes - настройки.reminder_window_minutes
    )
    конец = сейчас + timedelta(minutes=настройки.reminder_lead_minutes)

    сеансы = (
        (
            await сессия.execute(
                select(Session)
                .where(
                    Session.start_at >= начало,
                    Session.start_at <= конец,
                    Session.confirmed_at.is_(None),
                    Session.cancelled_at.is_(None),
                    Session.join_url.is_not(None),
                )
                .order_by(Session.start_at)
            )
        )
        .scalars()
        .all()
    )

    поставлено = 0
    for сеанс in сеансы:
        эксперт = await _эксперт(сессия, сеанс)
        if эксперт is None:
            continue

        строки = (
            await сессия.execute(
                select(Booking, EventType)
                .join(EventType, EventType.id == Booking.event_type_id)
                .where(
                    Booking.session_id == сеанс.id,
                    Booking.status.in_(Booking.СТАТУСЫ_С_МЕСТОМ),
                )
                .order_by(Booking.id)
            )
        ).all()

        время = _время_в_поясе(сеанс, эксперт.timezone)
        for заявка, вид in строки:
            if await _письмо_уже_стоит(сессия, заявка.id, ПИСЬМО_О_НАЧАЛЕ):
                continue
            сессия.add(
                OutboxEvent(
                    kind=ПИСЬМО_О_НАЧАЛЕ,
                    booking_id=заявка.id,
                    to_email=заявка.guest_email,
                    subject=mail.тема_письма(вид.name),
                    body=mail.текст_письма(
                        имя=заявка.guest_name,
                        встреча=вид.name,
                        эксперт=эксперт.display_name,
                        время=время,
                        ссылка=сеанс.join_url,
                    ),
                )
            )
            поставлено += 1

    if поставлено:
        await сессия.commit()
    return поставлено


async def _создать_встречу(
    сессия: AsyncSession,
    http: httpx.AsyncClient,
    сеанс: Session,
) -> telemost.Встреча | None:
    """Создать встречу в Телемосте токеном эксперта.

    Токен нужен именно эксперта: встреча создаётся от его имени, и он будет
    в ней организатором. Отказ не роняет всю задачу: сеанс помечается
    неудачей, чтобы по нему не пытались создать встречу снова каждый проход.
    """
    токен = (
        await сессия.execute(
            select(OAuthToken).where(OAuthToken.user_id == сеанс.owner_id)
        )
    ).scalar_one_or_none()

    if токен is None:
        сеанс.conference_status = "failed"
        журнал.warning("нет токена Яндекса у эксперта %s", сеанс.owner_id)
        return None

    try:
        return await telemost.создать_встречу(http, access_token=токен.access_token)
    except telemost.TelemostError as ошибка:
        сеанс.conference_status = "failed"
        журнал.warning("встреча не создана, сеанс %s: %s", сеанс.id, ошибка)
        return None


async def _эксперт(сессия: AsyncSession, сеанс: Session) -> User | None:
    """Владелец сеанса: его токеном создаётся встреча и его почта в письме."""
    return (
        await сессия.execute(select(User).where(User.id == сеанс.owner_id))
    ).scalar_one_or_none()


def _время_в_поясе(сеанс: Session, часовой_пояс: str) -> str:
    """Время начала в привычном для человека виде."""
    return сеанс.start_at.astimezone(ZoneInfo(часовой_пояс)).strftime(
        "%d.%m.%Y в %H:%M"
    )


async def _письмо_уже_стоит(
    сессия: AsyncSession, booking_id: int, kind: str
) -> bool:
    """Стоит ли уже в очереди письмо этого повода по этой заявке."""
    есть = (
        await сессия.execute(
            select(OutboxEvent.id)
            .where(OutboxEvent.booking_id == booking_id, OutboxEvent.kind == kind)
            .limit(1)
        )
    ).scalar_one_or_none()
    return есть is not None


def _новый_пароль() -> str:
    """Пароль встречи.

    Из букв и цифр без похожих символов: пароль читают вслух и вводят руками.
    """
    алфавит = string.ascii_letters + string.digits
    return "".join(secrets.choice(алфавит) for _ in range(8))


async def разослать(сессия: AsyncSession, настройки: Settings) -> int:
    """Отправить накопленные письма.

    Возвращает число отправленных. Неотправленные остаются в очереди и
    попробуют уйти в следующий проход.
    """
    письма = (
        (
            await сессия.execute(
                select(OutboxEvent)
                .where(
                    OutboxEvent.sent_at.is_(None),
                    OutboxEvent.attempts < ПОПЫТОК,
                )
                .order_by(OutboxEvent.id)
                .limit(ПАЧКА)
            )
        )
        .scalars()
        .all()
    )

    отправлено = 0
    for письмо in письма:
        письмо.attempts += 1
        try:
            mail.отправить(
                настройки,
                кому=письмо.to_email,
                тема=письмо.subject,
                текст=письмо.body,
            )
        except Exception as ошибка:  # noqa: BLE001
            # Причина может быть любой: сеть, ящик, лимит. Текст кладём в
            # очередь, чтобы не потерять письмо, и идём дальше.
            письмо.last_error = str(ошибка)[:500]
            журнал.warning("письмо %s не отправлено: %s", письмо.id, ошибка)
        else:
            письмо.sent_at = datetime.now(UTC)
            письмо.last_error = None
            отправлено += 1
    await сессия.commit()
    return отправлено


async def проход() -> None:
    """Один полный проход задачи."""
    настройки = get_settings()
    фабрика = async_sessionmaker(_движок(), expire_on_commit=False)
    async with фабрика() as сессия:
        await подготовить_встречи(сессия)
        await подготовить_напоминания(сессия, настройки)
        await разослать(сессия, настройки)


def _движок():
    from backend.db import get_engine

    return get_engine()


async def запустить(интервал_секунд: int = 60) -> None:
    """Крутить задачу, пока процесс жив.

    Интервал меньше минуты не нужен: встречу можно создать и с задержкой в
    минуту, а напоминание всё равно уходит в своём окне.
    """
    while True:
        try:
            await проход()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # Задача не должна падать целиком из-за одной неудачной встречи:
            # журнал получает причину, цикл продолжается.
            журнал.exception("проход задачи не удался")
        await asyncio.sleep(интервал_секунд)


if __name__ == "__main__":
    asyncio.run(запустить())
