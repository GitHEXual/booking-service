"""Публичная часть: страница вида встречи, сетка слотов и заявка.

Гость здесь не авторизуется. Он открывает ссылку эксперта, видит свободное
время и отправляет заявку с именем и почтой.

Правило простое: один слот это один гость. Проверяет его не код, а
уникальный индекс `uq_booking_active_session` на таблице заявок, поэтому две
одновременные заявки на одно время не пройдут обе, как бы быстро они ни
пришли.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.config import get_settings
from backend.crypto import слепой_индекс
from backend.db import get_session
from backend.models import Booking, EventType, Session
from backend.schemas import ЗаявкаВход
from backend.slots import МАКСИМУМ_ДНЕЙ_В_ЗАПРОСЕ, Правило, build_slots, find_slot

router = APIRouter(prefix="/api", tags=["гость"])


def _правило(вид: EventType, занятые: frozenset[datetime]) -> Правило:
    """Собрать правило расчёта из вида встречи и его расписания."""
    расписание = вид.schedule
    return Правило(
        weekdays=frozenset(int(день) for день in расписание.weekdays.split(",")),
        start_time=datetime.strptime(расписание.start_time, "%H:%M").time(),  # noqa: DTZ007
        end_time=datetime.strptime(расписание.end_time, "%H:%M").time(),  # noqa: DTZ007
        timezone=вид.owner.timezone,
        duration=timedelta(minutes=вид.duration_minutes),
        increment=timedelta(minutes=вид.time_increment_minutes),
        min_notice=timedelta(hours=вид.min_notice_hours),
        horizon=timedelta(days=вид.horizon_days),
        taken=занятые,
    )


async def _занятые(
    сессия: AsyncSession, вид: EventType, от: datetime, до: datetime
) -> frozenset[datetime]:
    """Начала слотов, на которые кто-то записан.

    Место держат только заявки в статусах `pending` и `confirmed`, поэтому
    отменённая или исключённая заявка освобождает время снова.
    """
    занятые = (
        await сессия.execute(
            select(Session.start_at)
            .join(Booking, Booking.session_id == Session.id)
            .where(
                Session.event_type_id == вид.id,
                Session.start_at >= от,
                Session.start_at < до,
                Booking.status.in_(Booking.СТАТУСЫ_С_МЕСТОМ),
            )
        )
    ).scalars().all()

    # Состав закреплён: время в сетке остаётся, но записаться на него нельзя.
    закрытые = (
        await сессия.execute(
            select(Session.start_at).where(
                Session.event_type_id == вид.id,
                Session.start_at >= от,
                Session.start_at < до,
                Session.confirmed_at.is_not(None),
            )
        )
    ).scalars().all()

    return frozenset(занятые) | frozenset(закрытые)


async def _вид_по_ссылке(сессия: AsyncSession, owner: str, slug: str) -> EventType:
    """Найти вид встречи по публичной ссылке `/api/{owner}/{slug}`."""
    вид = (
        await сессия.execute(
            select(EventType)
            .options(selectinload(EventType.schedule), selectinload(EventType.owner))
            .join(EventType.owner)
            .where(EventType.slug == slug, EventType.owner.has(login=owner))
        )
    ).scalar_one_or_none()

    if вид is None or not вид.active:
        raise HTTPException(404, "Такой ссылки нет")
    return вид


@router.get("/{owner}/{slug}")
async def о_виде_встречи(
    owner: str,
    slug: str,
    сессия: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Вид встречи для страницы гостя."""
    вид = await _вид_по_ссылке(сессия, owner, slug)
    return {
        "name": вид.name,
        "description": вид.description,
        "duration_minutes": вид.duration_minutes,
        "owner_name": вид.owner.display_name,
        "timezone": вид.owner.timezone,
    }


@router.get("/{owner}/{slug}/slots")
async def слоты_вида(
    owner: str,
    slug: str,
    дней: int = Query(default=7, ge=1, le=МАКСИМУМ_ДНЕЙ_В_ЗАПРОСЕ),
    сессия: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Сетка слотов на ближайшие дни.

    Диапазон ограничен: иначе ответ получится нечитаемым.
    """
    вид = await _вид_по_ссылке(сессия, owner, slug)
    now = datetime.now(UTC)
    занятые = await _занятые(сессия, вид, now, now + timedelta(days=вид.horizon_days))
    сетка = build_slots(_правило(вид, занятые), now=now)

    return {
        "timezone": вид.owner.timezone,
        # Время отдаётся в UTC вместе с поясом: перевод на пояс гостя делает
        # интерфейс, см. `docs/adr/0010-chasovye-ponya.md`.
        "slots": [
            {
                "start_at": слот.start_at.isoformat(),
                "end_at": слот.end_at.isoformat(),
                "is_taken": слот.is_taken,
                "can_request": слот.can_request,
            }
            for слот in сетка[:дней * 24]
        ],
    }


@router.post("/{owner}/{slug}/bookings", status_code=201)
async def отправить_заявку(
    owner: str,
    slug: str,
    данные: ЗаявкаВход,
    сессия: AsyncSession = Depends(get_session),
    настройки=Depends(get_settings),
) -> dict[str, object]:
    """Принять заявку гостя."""
    вид = await _вид_по_ссылке(сессия, owner, slug)
    now = datetime.now(UTC)

    занятые = await _занятые(сессия, вид, now, now + timedelta(days=вид.horizon_days))
    слот = find_slot(_правило(вид, занятые), данные.start_at, now=now)

    if слот is None:
        raise HTTPException(422, "Это время больше не доступно")
    if слот.is_taken:
        raise HTTPException(409, "На это время уже записан другой гость")

    # Сеанс появляется вместе с первой заявкой: самого слота в базе нет, и
    # хранить нечего, пока на него никто не записался.
    сеанс = (
        await сессия.execute(
            select(Session).where(
                Session.event_type_id == вид.id, Session.start_at == слот.start_at
            )
        )
    ).scalar_one_or_none()
    if сеанс is None:
        сеанс = Session(
            event_type_id=вид.id,
            owner_id=вид.owner_id,
            start_at=слот.start_at,
            end_at=слот.end_at,
        )
        сессия.add(сеанс)
        await сессия.flush()

    заявка = Booking(
        event_type_id=вид.id,
        owner_id=вид.owner_id,
        session_id=сеанс.id,
        guest_name=данные.name.strip(),
        guest_email=данные.email,
        guest_email_bidx=слепой_индекс(данные.email),
        guest_timezone=данные.timezone,
        consent_version=настройки.consent_document_version,
        consent_at=now,
    )
    сессия.add(заявка)

    try:
        await сессия.flush()
    except IntegrityError as ошибка:
        # Сюда попадают две ситуации: время заняли, пока страница была
        # открыта, и гость отправил форму дважды подряд. Обе означают одно:
        # заявка на это время уже есть.
        await сессия.rollback()
        raise HTTPException(409, "На это время уже есть заявка") from ошибка

    return {
        "id": заявка.id,
        "status": заявка.status,
        "start_at": слот.start_at.isoformat(),
        "end_at": слот.end_at.isoformat(),
    }
