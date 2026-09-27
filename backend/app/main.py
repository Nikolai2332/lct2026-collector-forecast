import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    auth,
    channels,
    dashboard,
    events,
    feedback,
    geo,
    data_in,
    journal,
    maintenance,
    model,
    notifications,
    objects,
    predictions,
    settings as settings_api,
    system,
    users,
    work_orders,
)
from app.api import sim as sim_api
from app import scheduler, schemas, sim
from app.recommendations import engine as recommendations
from app.db import SessionLocal
from app.config import get_settings, production_problems
from app.errors import translate_validation_errors
from app.notifications import broker
from app.timeutil import sim_moment

settings = get_settings()
logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)
# Такт симуляции — раз в секунду: сообщения APScheduler «Running job» засорили бы журнал. Ошибки заданий пишет app.scheduler
logging.getLogger("apscheduler").setLevel(logging.WARNING)

# Слабые секреты в production — отказ стартовать (entrypoint проверяет то же самое раньше, до миграций)
_problems = production_problems(settings)
if _problems:
    raise RuntimeError("Небезопасные настройки для APP_ENV=production:\n- " + "\n- ".join(_problems))


@asynccontextmanager
async def lifespan(_: FastAPI):
    broker.bind_loop(asyncio.get_running_loop())
    try:
        with SessionLocal() as db:
            sim.reset_on_startup(db)
    except Exception:  # без таблицы sim_state (схема не обновлена) API всё равно должен работать
        log.exception("Не удалось сбросить состояние симуляции")
    try:
        with SessionLocal() as db:
            recommendations.sync_dictionary(db)
    except Exception:  # файл правил с ошибкой или старая схема: API работает, рекомендации покажут ошибку
        log.exception("Не удалось синхронизировать справочник рекомендаций с правилами")
    scheduler.start(settings)
    yield
    scheduler.shutdown()


class SimMomentMiddleware:
    """Во время симуляции запрос без `at` получает модельное время (app.deps.at_param, app.services.outcome_out).

    Чистый ASGI: contextvar доходит до обработчиков и зависимостей в пуле потоков; SSE не буферизуется."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        moment = sim.model_now() if scope["type"] == "http" else None
        if moment is None or "at" in parse_qs(scope.get("query_string", b"").decode("latin-1")):
            await self.app(scope, receive, send)
            return
        token = sim_moment.set(moment)
        try:
            await self.app(scope, receive, send)
        finally:
            sim_moment.reset(token)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "REST API сервиса прогноза отказов датчиков коллекторов на 24 часа (ЛЦТ-2026).\n\n"
        "**Вход:** нажмите *Authorize* и введите логин и пароль (в демо-режиме — демо-пользователя из README), "
        "поля client_id/secret оставьте пустыми.\n\n"
        "**Машина времени:** все запросы на чтение принимают `at`. Демо-данные покрывают "
        "01.07.2026–31.08.2026, например `at=2026-08-01T12:00:00`.\n\n"
        "**Симуляция потока** (`/api/sim/*`): проигрывание суток по заранее рассчитанным прогнозам с ускорением; "
        "пока она идёт, запросы без `at` работают на модельное время. Онлайн-пересчёт моделью не выполняется.\n\n"
        "Время — московское, без часового пояса."
    ),
    lifespan=lifespan,
    # Swagger и схема — в demo; в production выключены (DOCS_ENABLED=true включает явно), см. docs/SECURITY.md
    docs_url="/docs" if settings.docs_on else None,
    redoc_url="/redoc" if settings.docs_on else None,
    openapi_url="/openapi.json" if settings.docs_on else None,
    swagger_ui_parameters={"persistAuthorization": True, "displayRequestDuration": True},
)

app.add_middleware(
    CORSMiddleware,
    # Явный список источников; cookie не используются (токен в заголовке Authorization), поэтому без credentials
    allow_origins=[o for o in settings.cors_origin_list if o != "*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
    expose_headers=["Content-Disposition"],
)
app.add_middleware(SimMomentMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": translate_validation_errors(exc.errors())})


@app.exception_handler(OverflowError)
async def overflow_handler(_: Request, exc: OverflowError):
    # Дата у границы календаря (at=0001-01-01 минус 30 дней) — ошибка запроса, а не сервера (аудит 2, S-34)
    return JSONResponse(status_code=422, content={"detail": "Дата вне допустимого диапазона"})


for module in (system, auth, dashboard, objects, channels, predictions, journal, work_orders, maintenance, model, data_in,
               notifications, sim_api, settings_api, users, geo, events, feedback):
    app.include_router(module.router)


_base_openapi = app.openapi


def openapi_with_manual_bodies():
    """Схема OpenAPI + модели тел, которые разбираются вручную (поток событий: JSON или XML по Content-Type)."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = _base_openapi()
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    extra = schemas.EventsIn.model_json_schema(ref_template="#/components/schemas/{model}")
    for name, sub in extra.pop("$defs", {}).items():
        components.setdefault(name, sub)
    components.setdefault("EventsIn", extra)
    return schema


app.openapi = openapi_with_manual_bodies
