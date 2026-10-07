# Сборка интерфейса идёт в отдельном шаге: Node нужен только здесь, в готовом
# образе его нет. Собранный `dist` кладётся рядом с сервисом, и тот отдаёт его
# сам, поэтому деплой это один контейнер.
FROM node:22-slim AS web

WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

RUN apt-get update \
    && apt-get install -y --no-install-recommends make \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"

COPY alembic.ini ./
COPY backend ./backend
COPY tests ./tests
# Собранный интерфейс. Отсутствие каталога означает, что сервис отдаёт только
# API, как в разработке, где интерфейс запускает Vite.
COPY --from=web /web/dist ./frontend/dist

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
