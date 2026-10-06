"""Фоновая задача: за несколько минут до встречи создать видеовстречу и
разослать гостям ссылки.

Задача одна, поэтому и запускается одним проходом: находит сеансы, которые
скоро начнутся, создаёт для каждого встречу и кладёт письма в очередь.
Письма отправляет отдельный проход, чтобы сбой отправки не сдвигал создание
встреч и наоборот.
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
from backend.models import Booking, EventType, OAuthToken, OutboxEvent, Session

журнал = logging.getLogger("worker")

# За сколько минут до встречи рассылаем ссылку. Пять минут хватает, чтобы
# человек успел открыть письмо и что-то прочитать, но мало для того, чтобы
# забыть о встрече.
ЗА_СКОЛЬКО_МИНУТ = 5

# Насколько широким окном ловим встречи. Если проход повторится или
# проспит, встречи не потеряются: окно шире, чем запас.
ОКНО_МИНУТ = 7

# Сколько писем отправляем за один проход. Без предела SMTP на большом
# количестве гостей упрётся в лимит и откажет всё сразу.
ПАЧКА = 50

# Сколько раз пробуем отправить письмо, прежде чем признать неудачным.
# Три попытки: почта может подвиснуть на пару минут.
ПОПЫТОК = 3


async def подготовить_встречи(сессия: AsyncSession, настройки: Settings) -> int:
    """Создать встречи для сеансов, которые скоро начнутся.

    Возвращает число созданных встреч. Уже созданные и заполненные вручную
    пропускаются: повторный проход не должен их перетирать.
    """
    сейчас = datetime.now(UTC)
    начало = сейчас + timedelta(minutes=ЗА_СКОЛЬКО_МИНУТ - ОКНО_МИНУТ)
    конец = сейчас + timedelta(minutes=ЗА_СКОЛЬКО_МИНУТ)

    сеансы = (
        (
            await сессия.execute(
                select(Session)
                .where(
                    Session.start_at >= начало,
                    Session.start_at <= конец,
                    Session.confirmed_at.is_(None),
                    Session.cancelled_at.is_(None),
                    Session.join_url.is_(None),
                )
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
            await _подготовить_сеанс(сессия, http, сеанс, настройки)
    await сессия.commit()
    return len(сеансы)


async def _подготовить_сеанс(
    сессия: AsyncSession,
    http: httpx.AsyncClient,
    сеанс: Session,
    настройки: Settings,
) -> None:
    """Создать встречу для одного сеанса и разослать письма."""
    # Ссылка могла прийти с ручным способом, и тогда письма уже могли уйти.
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
            )
        )
        .all()
    )
    if not заявки:
        return

    встреча = await _создать_встречу(сессия, http, сеанс, заявки[0][1], настройки)
    if встреча is None:
        return

    сеанс.conference_id = встреча.conference_id
    сеанс.join_url = встреча.join_url
    сеанс.meeting_password = _новый_пароль()
    сеанс.conference_status = "ready"

    пояс = await _пояс_эксперта(сессия, сеанс)
    время = сеанс.start_at.astimezone(ZoneInfo(пояс)).strftime("%d.%m.%Y в %H:%M")

    for заявка, вид in заявки:
        сессия.add(
            OutboxEvent(
                kind="meeting_soon",
                booking_id=заявка.id,
                to_email=заявка.guest_email,
                subject=mail.тема_письма(вид.name),
                body=mail.текст_письма(
                    имя=заявка.guest_name,
                    встреча=вид.name,
                    эксперт=вид.owner.display_name if вид.owner else "экспер��",
                    время=время,
                    ссылка=встреча.join_url,
                ),
            )
        )

    # Организатору письмо тоже: он ведёт встречу и без своей ссылки её не
    # начать. Текст другой: гостя зовут подключиться, организатора оповещают.
    почта_эксперта = await _почта_эксперта(сессия, сеанс)
    if почта_эксперта:
        сессия.add(
            OutboxEvent(
                kind="meeting_soon",
                booking_id=заявки[0][0].id,
                to_email=почта_эксперта,
                subject=mail.тема_письма(заявки[0][1].name),
                body=mail.текст_письма_эксперту(
                    встреча=заявки[0][1].name,
                    гости=len(заявки),
                    время=время,
                    ссылка=встреча.join_url,
                ),
            )
        )

    журнал.info(
        "встреча создана, сеанс %s, гостей %s", сеанс.id, len(заявки)
    )


async def _создать_встречу(
    сессия: AsyncSession,
    http: httpx.AsyncClient,
    сеанс: Session,
    вид: EventType,
    настройки: Settings,
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


async def _почта_эксперта(сессия: AsyncSession, сеанс: Session) -> str | None:
    """Почта эксперта для письма ему же."""
    from backend.models import User

    эксперт = (
        await сессия.execute(select(User).where(User.id == сеанс.owner_id))
    ).scalar_one_or_none()
    return эксперт.email if эксперт else None


async def _пояс_эксперта(сессия: AsyncSession, сеанс: Session) -> str:
    """Часовой пояс эксперта, чтобы показать время в его привычном виде."""
    from backend.models import User

    эксперт = (
        await сессия.execute(select(User).where(User.id == сеанс.owner_id))
    ).scalar_one_or_none()
    return эксперт.timezone if эксперт else "UTC"


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
        await подготовить_встречи(сессия, настройки)
        await разослать(сессия, настройки)


def _движок():
    from backend.db import get_engine

    return get_engine()


async def запустить(интервал_секунд: int = 60) -> None:
    """Крутить задачу, пока процесс жив.

    Интервал меньше минуты не нужен: окно в семь минут и письма, зависшие
    после сбоя почты, всё равно уйдут в следующем проходе.
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
