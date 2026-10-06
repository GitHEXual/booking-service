"""Сборка приложения."""

from fastapi import FastAPI

from backend.routes.auth import router as auth_router


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
    return приложение


app = create_app()