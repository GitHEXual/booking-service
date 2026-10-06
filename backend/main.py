"""Сборка приложения."""

from fastapi import FastAPI

from backend.routes.auth import router as auth_router
from backend.routes.guest import router as guest_router
from backend.routes.panel import router as panel_router


def create_app() -> FastAPI:
    приложение = FastAPI(
        title="Сервис записи на встречи",
        description=(
            "Эксперт задаёт виды встреч и часы приёма, гость отправляет заявку "
            "на свободный слот."
        ),
        version="0.1.0",
    )
    приложение.include_router(auth_router)
    # Панель подключается раньше гостевой части намеренно: у гостя путь
    # `/api/{owner}/{slug}`, и он перехватывал бы `/api/panel/event-types`,
    # если бы шёл первым. Конкретные пути должны проверяться раньше общих.
    приложение.include_router(panel_router)
    приложение.include_router(guest_router)
    return приложение


app = create_app()