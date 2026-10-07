"""Панель эксперта: виды встреч, расписание и заявки.

Всё, что здесь есть, доступно только вошедшему эксперту и только в отношении
его собственных данных.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.db import get_session
from backend.models import (
    Booking,
    EventType,
    Schedule,
    Session,
    User,
)
from backend.routes.auth import require_expert
from backend.schemas import ВидВстречиВход, НастройкиВход

router = APIRouter(prefix="/api/panel", tags=["панель"])


@router.get("/event-types")
async def мои_виды(
    эксперт: User = Depends(require_expert),
    сессия: AsyncSession = Depends(get_session, scope="function"),
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
            "active": вид.active,
            "public_path": f"/u/{эксперт.login}/{вид.slug}",
        }
        for вид in виды
    ]


@router.post("/event-types", status_code=201)
async def создать_вид(
    данные: ВидВстречиВход,
    эксперт: User = Depends(require_expert),
    сессия: AsyncSession = Depends(get_session, scope="function"),
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


@router.delete("/event-types/{event_type_id}", status_code=204)
async def удалить_вид(
    event_type_id: int,
    эксперт: User = Depends(require_expert),
    сессия: AsyncSession = Depends(get_session, scope="function"),
) -> None:
    """Удалить вид встречи вместе с его заявками.

    Заявки, сеансы и уже созданные письма удаляются каскадом на стороне базы.
    Расписание эксперта остаётся: оно общее для всех его видов встреч.
    """
    вид = (
        await сессия.execute(
            select(EventType).where(
                EventType.id == event_type_id, EventType.owner_id == эксперт.id
            )
        )
    ).scalar_one_or_none()

    if вид is None:
        raise HTTPException(404, "Такой встречи нет")

    await сессия.delete(вид)


@router.get("/bookings")
async def мои_заявки(
    эксперт: User = Depends(require_expert),
    сессия: AsyncSession = Depends(get_session, scope="function"),
) -> list[dict[str, object]]:
    """Заявки гостей, ожидающие решения.

    Сортировка по времени встречи: так эксперт смотрит на список в порядке,
    в котором встречи будут проходить. Ссылка на встречу отдаётся сразу:
    панель показывает её как ближайшую встречу с кнопкой подключения.
    """
    строки = (
        await сессия.execute(
            select(Booking, Session, EventType)
            .join(Session, Session.id == Booking.session_id)
            .join(EventType, EventType.id == Booking.event_type_id)
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
            "event_type_name": вид.name,
            "name": заявка.guest_name,
            "email": заявка.guest_email,
            "status": заявка.status,
            "start_at": сеанс.start_at.isoformat(),
            "end_at": сеанс.end_at.isoformat(),
            "timezone": заявка.guest_timezone,
            "join_url": сеанс.join_url,
            "conference_status": сеанс.conference_status,
        }
        for заявка, сеанс, вид in строки
    ]


@router.delete("/bookings/{booking_id}", status_code=204)
async def удалить_заявку(
    booking_id: int,
    эксперт: User = Depends(require_expert),
    сессия: AsyncSession = Depends(get_session, scope="function"),
) -> None:
    """Убрать заявку гостя и освободить её время.

    Если в слоте больше никого не осталось, удаляется и сеанс: сеанс это
    хранимая запись о занятом времени, и без заявок хранить нечего. Вместе с
    ним уходит и созданная ранее встреча, иначе следующая заявка на то же
    время переиспользовала бы чужую ссылку на Телемост.
    """
    заявка = (
        await сессия.execute(
            select(Booking).where(
                Booking.id == booking_id, Booking.owner_id == эксперт.id
            )
        )
    ).scalar_one_or_none()

    if заявка is None:
        raise HTTPException(404, "Такой заявки нет")

    session_id = заявка.session_id
    await сессия.delete(заявка)
    await сессия.flush()

    осталось = (
        await сессия.execute(
            select(func.count())
            .select_from(Booking)
            .where(Booking.session_id == session_id)
        )
    ).scalar_one()

    if осталось == 0:
        сеанс = (
            await сессия.execute(select(Session).where(Session.id == session_id))
        ).scalar_one_or_none()
        if сеанс is not None:
            await сессия.delete(сеанс)


@router.put("/settings")
async def сохранить_настройки(
    тело: НастройкиВход,
    эксперт: User = Depends(require_expert),
    сессия: AsyncSession = Depends(get_session, scope="function"),
) -> dict[str, object]:
    """Сохранить часовой пояс эксперта.

    От него зависит всё расписание: часы приёма хранятся локальными, и без
    пояса сетка получается в чужом времени.
    """
    эксперт.timezone = тело.timezone
    await сессия.flush()
    return {"timezone": эксперт.timezone}


@router.get("/schedule")
async def моё_расписание(
    эксперт: User = Depends(require_expert),
    сессия: AsyncSession = Depends(get_session, scope="function"),
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
