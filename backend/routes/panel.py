"""Панель эксперта: виды встреч, расписание и заявки.

Всё, что здесь есть, доступно только вошедшему эксперту и только в отношении
его собственных данных.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.db import get_session
from backend.models import Booking, EventType, Schedule, Session, User
from backend.routes.auth import require_expert
from backend.schemas import ВидВстречиВход

router = APIRouter(prefix="/api/panel", tags=["панель"])


@router.get("/event-types")
async def мои_виды(
    эксперт: User = Depends(require_expert),
    сессия: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    """Виды встреч эксперта вместе с их ссылками."""
    виды = (
        await сессия.execute(
            select(EventType)
            .options(selectinload(EventType.owner))
            .where(EventType.owner_id == эксперт.id)
            .order_by(EventType.id)
        )
    ).scalars().all()

    return [
        {
            "id": вид.id,
            "name": вид.name,
            "slug": вид.slug,
            "description": вид.description,
            "duration_minutes": вид.duration_minutes,
            "max_guests": вид.max_guests,
            "active": вид.active,
            "public_path": f"/u/{эксперт.login}/{вид.slug}",
        }
        for вид in виды
    ]


@router.post("/event-types", status_code=201)
async def создать_вид(
    данные: ВидВстречиВход,
    эксперт: User = Depends(require_expert),
    сессия: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Создать вид встречи и его расписание.

    Расписание принадлежит эксперту, поэтому оно создаётся вместе с первым его
    видом встречи и переиспользуется следующими.
    """
    расписание = (
        await сессия.execute(
            select(Schedule)
            .where(Schedule.owner_id == эксперт.id)
            .order_by(Schedule.id)
            .limit(1)
        )
    ).scalar_one_or_none()

    if расписание is None:
        расписание = Schedule(
            owner_id=эксперт.id,
            weekdays=",".join(str(день) for день in данные.schedule.weekdays),
            start_time=данные.schedule.start_time,
            end_time=данные.schedule.end_time,
        )
        сессия.add(расписание)
        await сессия.flush()
    else:
        # Часы приёма общие для всех видов встреч, поэтому новый вид встречи
        # обновляет их, а не создаёт второе расписание.
        расписание.weekdays = ",".join(
            str(день) for день in данные.schedule.weekdays
        )
        расписание.start_time = данные.schedule.start_time
        расписание.end_time = данные.schedule.end_time

    вид = EventType(
        owner_id=эксперт.id,
        schedule_id=расписание.id,
        name=данные.name.strip(),
        slug=данные.slug,
        description=данные.description,
        duration_minutes=данные.duration_minutes,
        time_increment_minutes=данные.time_increment_minutes,
        max_guests=данные.max_guests,
        min_notice_hours=данные.min_notice_hours,
        horizon_days=данные.horizon_days,
    )
    сессия.add(вид)

    try:
        await сессия.flush()
    except IntegrityError as ошибка:
        # Слаг уникален в паре с владельцем, поэтому одинаковая ссылка у одного
        # эксперта невозможна, а у разных экспертов возможна и не проверяется.
        await сессия.rollback()
        raise HTTPException(409, "Такая ссылка уже занята") from ошибка

    return {
        "id": вид.id,
        "name": вид.name,
        "slug": вид.slug,
        "public_path": f"/u/{эксперт.login}/{вид.slug}",
    }


@router.get("/bookings")
async def мои_заявки(
    эксперт: User = Depends(require_expert),
    сессия: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    """Заявки гостей, ожидающие решения.

    Сортировка по времени встречи: так эксперт смотрит на список в порядке,
    в котором встречи будут проходить.
    """
    строки = (
        await сессия.execute(
            select(Booking, Session)
            .join(Session, Session.id == Booking.session_id)
            .where(
                Booking.owner_id == эксперт.id,
                Booking.status.in_(Booking.СТАТУСЫ_С_МЕСТОМ),
            )
            .order_by(Session.start_at)
        )
    ).all()

    return [
        {
            "id": заявка.id,
            "event_type_id": заявка.event_type_id,
            "name": заявка.guest_name,
            "email": заявка.guest_email,
            "status": заявка.status,
            "start_at": заявка.session.start_at.isoformat(),
            "end_at": заявка.session.end_at.isoformat(),
            "timezone": заявка.guest_timezone,
        }
        for заявка, сеанс in строки
    ]


@router.get("/schedule")
async def моё_расписание(
    эксперт: User = Depends(require_expert),
    сессия: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Часы приёма эксперта."""
    расписание = (
        await сессия.execute(
            select(Schedule).where(Schedule.owner_id == эксперт.id).limit(1)
        )
    ).scalar_one_or_none()

    if расписание is None:
        raise HTTPException(404, "Расписание ещё не задано")

    return {
        "weekdays": [int(день) for день in расписание.weekdays.split(",")],
        "start_time": расписание.start_time,
        "end_time": расписание.end_time,
        "timezone": эксперт.timezone,
    }
