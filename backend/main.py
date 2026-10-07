"""Сборка приложения."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from backend.routes.auth import router as auth_router
from backend.routes.guest import router as guest_router
from backend.routes.panel import router as panel_router

# Собранный интерфейс. В образе он есть, в разработке его отдаёт Vite, и тогда
# каталога нет.
ВЕБ_КАТАЛОГ = Path(__file__).resolve().parent.parent / "frontend" / "dist"

# Адреса, которые фронтенду не отдаём: их обрабатывает само приложение.
СЛУЖЕБНЫЕ = ("api/", "auth/", "docs", "redoc", "openapi.json")


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
    _подключить_интерфейс(приложение)
    return приложение


def _подключить_интерфейс(приложение: FastAPI) -> None:
    """Отдавать собранный интерфейс, если он есть рядом с сервисом.

    Правило добавлено последним, поэтому конкретные маршруты API совпадают
    раньше него. Неизвестный путь отдаёт `index.html`: у интерфейса своя
    маршрутизация, и страница гостя вида `/u/{логин}/{адрес}` должна
    открываться, даже если файла по этому пути нет.
    """
    if not ВЕБ_КАТАЛОГ.is_dir():
        return

    индекс = ВЕБ_КАТАЛОГ / "index.html"

    # Имя параметра маршрута только латиницей: Starlette разбирает путь
    # регуляркой, которая кириллицу в имени не принимает, и тогда маршрут
    # становится буквальным и никогда не совпадает.
    @приложение.get("/{full_path:path}", include_in_schema=False)
    async def страница(full_path: str) -> FileResponse:
        if full_path.startswith(СЛУЖЕБНЫЕ):
            raise HTTPException(404, "Не найдено")
        файл = ВЕБ_КАТАЛОГ / full_path
        if full_path and файл.is_file():
            return FileResponse(файл)
        return FileResponse(индекс)


app = create_app()
