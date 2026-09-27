from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import cache, schemas, xmlio
from app.audit import audit
from app.access import require_user
from app.db import get_db
from app.config import get_settings
from app.importer import clean_event, import_file, known_channel_ids, safe_file_name, store_events
from app.models import User
from app.notifications import publish_critical
from app.sim import record_ingest
from app.ratelimit import uploads
from app.timeutil import to_msk_naive

router = APIRouter(tags=["Загрузка данных"])

MAX_IMPORT_BYTES = 50 * 1024 * 1024
# Пачка до 10 000 событий в JSON — около 1,5 МБ, в XML — около 2,5 МБ
MAX_INGEST_BYTES = 10 * 1024 * 1024

EVENTS_XML_EXAMPLE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<events>\n  <event>\n    <event_id>3008235019</event_id>\n'
    '    <channel_id>334609</channel_id>\n    <ts>2026-08-01T12:00:05</ts>\n    <is_alarm>false</is_alarm>\n'
    '    <value>Норма</value>\n  </event>\n</events>'
)
INGEST_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/EventsIn"}},
            "application/xml": {"schema": {"type": "string"}, "example": EVENTS_XML_EXAMPLE},
        },
    }
}


async def read_limited(request: Request, limit: int) -> bytes:
    """Тело запроса, но не больше limit байт: большее — 413 до разбора."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Тело запроса больше {limit // (1024 * 1024)} МБ")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Тело запроса больше {limit // (1024 * 1024)} МБ")
        chunks.append(chunk)
    return b"".join(chunks)


def _with_body_loc(errors: list) -> list:
    return [{**e, "loc": ("body", *e.get("loc", ()))} for e in errors]


async def events_body(request: Request) -> schemas.EventsIn:
    """JSON или XML по Content-Type; проверка одной и той же схемой EventsIn (ошибки — 422 в формате FastAPI)."""
    raw = await read_limited(request, MAX_INGEST_BYTES)
    content_type = request.headers.get("content-type", "")
    try:
        if xmlio.is_xml_content_type(content_type):
            return schemas.EventsIn.model_validate(xmlio.events_payload(raw))
        if content_type.split(";")[0].strip().lower() in ("application/json", ""):
            return schemas.EventsIn.model_validate_json(raw)
    except ValidationError as e:
        raise RequestValidationError(_with_body_loc(e.errors(include_url=False)))
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e))
    raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Поток событий принимается в JSON или XML (application/xml)")


@router.post(
    "/api/ingest/events",
    response_model=schemas.IngestResult,
    summary="Приём потока событий (JSON или XML)",
    description=(
        "Пачка до 10 000 событий (до 10 МБ). Формат — по заголовку Content-Type: `application/json` или "
        "`application/xml` / `text/xml` (`<events><event>…</event></events>`, поля те же, что в JSON). XML разбирается "
        "безопасно: DTD и сущности запрещены (XXE, «миллиард смешков» — 422). Чистка как у импорта: значения-даты "
        "выбрасываются, флаг f/t и false/true, дубли (канал, время, значение) пропускаются, неизвестные каналы "
        "отбрасываются."
    ),
    openapi_extra=INGEST_OPENAPI,
    responses={413: {"model": schemas.ErrorResponse}, 415: {"model": schemas.ErrorResponse}},
)
async def ingest_events(
    request: Request,
    user: User = Depends(require_user("data_import")),
    db: Session = Depends(get_db),
):
    body = await events_body(request)
    # Запись в БД — синхронная: в пуле потоков, не блокируя цикл событий
    return await run_in_threadpool(store_ingest, body, request, user, db)


def store_ingest(body: schemas.EventsIn, request: Request, user: User, db: Session) -> schemas.IngestResult:
    known = known_channel_ids(db, {e.channel_id for e in body.events})
    clean, unknown, invalid = [], 0, 0
    for e in body.events:
        if e.channel_id not in known:
            unknown += 1
            continue
        try:
            ev = clean_event(e.channel_id, to_msk_naive(e.ts), e.is_alarm, e.value, e.event_id)
        except ValueError:
            ev = None
        if ev is None:
            invalid += 1
            continue
        clean.append(ev)
    inserted, duplicates, affected = store_events(db, clean)
    # Онлайн-пересчёта моделью нет (признакам нужна история, которой у жюри нет): прогнозы рассчитаны заранее,
    # критические уведомления во время симуляции рассылает app.sim по модельным часам
    record_ingest(db, len(body.events), inserted)
    audit(db, request, user, "ingest_events", "events_recent",
          details={"received": len(body.events), "accepted": inserted})
    db.commit()
    return schemas.IngestResult(
        received=len(body.events),
        accepted=inserted,
        duplicates=duplicates,
        skipped_unknown_channel=unknown,
        skipped_invalid=invalid,
        affected_channels=affected,
    )


@router.post(
    "/api/import",
    response_model=schemas.ImportResult,
    summary="Ручная загрузка CSV, XLSX или XML",
    description=(
        "`kind`: objects — справочник объектов, channels — справочник каналов, events — журнал событий, "
        "predictions — таблица прогнозов от ML. Заголовки — как в файлах заказчика "
        "(ид_канала_данных, дата, время, тревожное, значение_датчика …) или английские имена полей. "
        "CSV в UTF-8, разделитель запятая или точка с запятой. XML — `<rows><row><поле>значение</поле>…</row></rows>` "
        "(имена полей — те же, что заголовки CSV; DTD и сущности запрещены). "
        "До 50 МБ и 300 000 строк; большие журналы — через ETL. "
        "Тип файла проверяется по содержимому; не больше 20 загрузок за 10 минут на пользователя."
    ),
    responses={
        413: {"model": schemas.ErrorResponse},
        422: {"model": schemas.ErrorResponse},
        429: {"model": schemas.ErrorResponse},
    },
)
def import_data(
    request: Request,
    kind: Literal["objects", "channels", "events", "predictions"] = Form(...),
    file: UploadFile = File(..., description="Файл .csv, .xlsx или .xml"),
    user: User = Depends(require_user("data_import")),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    key, window = f"user:{user.id}", settings.import_window_seconds
    if uploads.count(key, window) >= settings.import_max_per_window:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Слишком много загрузок подряд. Повторите через несколько минут.",
            headers={"Retry-After": str(uploads.retry_after(key, window))},
        )
    uploads.hit(key)
    # Синхронный обработчик: разбор файла и запись в БД идут в пуле потоков, не блокируя цикл событий
    content = file.file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Файл больше 50 МБ — загрузите его через ETL")
    file_name = safe_file_name(file.filename)
    try:
        report = import_file(db, kind, file_name, content)
    except UnicodeDecodeError:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "CSV должен быть в кодировке UTF-8")
    except ValueError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e))
    audit(db, request, user, "import", kind, details={
        "file": file_name, "rows_total": report.rows_total, "rows_imported": report.rows_imported,
        "rows_skipped": report.rows_skipped,
    })
    db.commit()
    cache.bump()
    if report.imported_predictions:
        publish_critical(db, report.imported_predictions)
    return schemas.ImportResult(
        kind=kind,
        file_name=file_name,
        rows_total=report.rows_total,
        rows_imported=report.rows_imported,
        rows_skipped=report.rows_skipped,
        errors=[schemas.ImportError_(**e) for e in report.errors],
    )
