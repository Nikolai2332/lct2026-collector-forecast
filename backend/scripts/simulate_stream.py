"""Симулятор потока событий: проигрывает сутки журнала в POST /api/ingest/events с ускорением.

Что происходит: скрипт запускает симуляцию (POST /api/sim/start), и на сервере начинают идти модельные часы
(×60 — сутки за 24 минуты). Скрипт отправляет события суток порциями, когда модельное время доходит до их метки.
Прогнозы при этом НЕ пересчитываются моделью: сервер показывает заранее рассчитанные прогнозы на модельный момент
и рассылает новые критические в SSE (см. app/sim.py). По Ctrl+C и по окончании суток симуляция останавливается.

Источник событий — таблица events_recent (по умолчанию) или CSV в формате журнала (--file). Режим по умолчанию
неразрушающий: события, которые уже есть в базе, сервер пропускает как дубли и честно их считает.

Запуск:
  docker compose exec api python -m scripts.simulate_stream --day 2026-06-15 --speed 60
  cd backend && .venv/Scripts/python -m scripts.simulate_stream --speed 600            (локально, API на :8000)

Логин и пароль: --user/--password или SIM_USERNAME/SIM_PASSWORD (роль engineer или admin).
Адрес API: --api или SIM_API_URL (по умолчанию http://localhost:8000).
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

BATCH_MAX = 5000
STATE_SYNC_SECONDS = 5.0
# Не чаще раза в секунду: каждая пачка — запись ingest_events в audit_log (≈150 записей за сутки на ×600)
SEND_SECONDS = 1.0
PRINT_SECONDS = 2.0


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


class Api:
    def __init__(self, base: str):
        if not base.lower().startswith(("http://", "https://")):
            # urlopen умеет и file://, и ftp:// — адрес API берётся только http(s) (bandit B310, аудит 2)
            raise ApiError(0, f"адрес API должен начинаться с http:// или https://: {base}")
        self.base = base.rstrip("/")
        self.token: str | None = None

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 60):
        data = json.dumps(body, ensure_ascii=False, default=str).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 — схема проверена в __init__
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read()).get("detail")
            except Exception:
                detail = e.reason
            if isinstance(detail, list):  # ошибки валидации
                detail = "; ".join(str(d.get("msg", d)) for d in detail)
            raise ApiError(e.code, str(detail)) from None
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(0, f"API недоступен по адресу {self.base}: {getattr(e, 'reason', e)}") from None

    def login(self, username: str, password: str) -> None:
        self.token = self.call("POST", "/api/auth/login", {"username": username, "password": password})["access_token"]


# ---------- Источник событий ----------


def events_from_db(day: date) -> list[dict]:
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import EventRecent

    start = datetime.combine(day, datetime.min.time())
    with SessionLocal() as db:
        rows = db.execute(
            select(EventRecent.source_event_id, EventRecent.channel_id, EventRecent.ts, EventRecent.is_alarm,
                   EventRecent.value_raw)
            .where(EventRecent.ts >= start, EventRecent.ts < start + timedelta(days=1))
            .order_by(EventRecent.ts, EventRecent.id)
        ).all()
    return [{"event_id": r[0], "channel_id": r[1], "ts": r[2], "is_alarm": r[3], "value": r[4]} for r in rows]


def events_from_file(path: Path, day: date) -> list[dict]:
    from app.importer import clean_event, map_columns, parse_date, parse_datetime, parse_time, read_table

    rows = map_columns(read_table(path.read_bytes(), path.name), "events")
    events, skipped = [], 0
    for r in rows:
        try:
            ts = parse_datetime(r["ts"]) if r.get("ts") else datetime.combine(parse_date(r["date"]), parse_time(r["time"]))
            ev = clean_event(r["channel_id"], ts, r.get("is_alarm", False), r.get("value"), r.get("event_id"))
        except (KeyError, ValueError):
            skipped += 1
            continue
        if ev is None or ev.ts.date() != day:
            skipped += ev is None
            continue
        events.append({"event_id": ev.source_event_id, "channel_id": ev.channel_id, "ts": ev.ts,
                       "is_alarm": ev.is_alarm, "value": ev.value_raw})
    if skipped:
        print(f"В файле пропущено строк с ошибками: {skipped}")
    events.sort(key=lambda e: e["ts"])
    return events


def purge_day(day: date, events: list[dict]) -> int:
    """Удаляет события суток из events_recent (только их). Перед удалением сохраняет их в CSV для восстановления."""
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models import EventRecent

    backup = Path(f"sim_backup_events_{day.isoformat()}.csv").resolve()
    with backup.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["event_id", "channel_id", "ts", "is_alarm", "value"])
        for e in events:
            w.writerow([e["event_id"] or "", e["channel_id"], e["ts"].isoformat(), "true" if e["is_alarm"] else "false",
                        e["value"]])
    start = datetime.combine(day, datetime.min.time())
    with SessionLocal() as db:
        n = db.execute(
            delete(EventRecent).where(EventRecent.ts >= start, EventRecent.ts < start + timedelta(days=1))
        ).rowcount
        db.commit()
    print(f"Удалено событий за {day:%d.%m.%Y}: {n}. Копия для восстановления: {backup}")
    print(f"  восстановить: python -m scripts.simulate_stream --day {day} --file {backup.name} (или импорт CSV в интерфейсе)")
    return n


# ---------- Проигрывание ----------


def parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def payload(e: dict) -> dict:
    return {"event_id": e["event_id"], "channel_id": e["channel_id"], "ts": e["ts"].isoformat(),
            "is_alarm": bool(e["is_alarm"]), "value": e["value"]}


def run(args) -> int:
    try:
        api = Api(args.api)
        api.login(args.user, args.password)
    except ApiError as e:
        print(f"Не удалось войти как {args.user!r}: {e.detail}", file=sys.stderr)
        return 2
    state = api.call("GET", "/api/sim/state")
    if not state["enabled"]:
        print("Симуляция выключена на сервере (SIM_ENABLED=false).", file=sys.stderr)
        return 2
    day = args.day or (date.fromisoformat(state["default_day"]) if state["default_day"] else None)
    if day is None:
        print("В базе нет прогнозов — проигрывать нечего.", file=sys.stderr)
        return 2
    print(f"Сутки {day:%d.%m.%Y}, ускорение ×{args.speed}. Прогнозы с {state['available_from']} по {state['available_to']}.")
    print("Воспроизведение заранее рассчитанных прогнозов; онлайн-пересчёт моделью не выполняется.")

    try:
        events = events_from_file(Path(args.file), day) if args.file else events_from_db(day)
    except Exception as e:  # нет доступа к БД или плохой файл
        print(f"Не удалось прочитать события: {e}", file=sys.stderr)
        return 2
    print(f"Событий за сутки: {len(events)} ({'файл ' + args.file if args.file else 'таблица events_recent'})")
    if args.purge_day:
        if not args.yes:
            print("--purge-day удаляет события этих суток из events_recent; подтвердите флагом --yes.", file=sys.stderr)
            return 2
        if args.file:
            print("--purge-day используется только без --file (события берутся из events_recent).", file=sys.stderr)
            return 2
        purge_day(day, events)

    attached = False
    try:
        state = api.call("POST", "/api/sim/start", {"day": day.isoformat(), "speed": args.speed})
    except ApiError as e:
        current = api.call("GET", "/api/sim/state")
        if e.status == 409 and current["active"] and current["day"] == day.isoformat():
            print(f"Симуляция этих суток уже идёт (запустил {current['started_by']}) — подключаюсь к ней.")
            state, attached = current, True
        else:
            print(f"Не удалось запустить симуляцию: {e.detail}", file=sys.stderr)
            return 2

    speed = state["speed"]
    model = parse_dt(state["model_time"])
    synced = time.monotonic()
    sent = accepted = duplicates = skipped = 0
    i, last_print, last_sync, last_send = 0, 0.0, synced, 0.0
    # Подключились к идущей симуляции — события до текущего модельного момента уже прошли.
    # При своём запуске не пропускаем ничего: пока шёл ответ, модельные часы успели уйти на секунды вперёд
    while attached and i < len(events) and model and events[i]["ts"] < model:
        i += 1
    finished = False
    try:
        while True:
            now = time.monotonic()
            if now - last_sync >= STATE_SYNC_SECONDS:
                state = api.call("GET", "/api/sim/state")
                model, synced, last_sync = parse_dt(state["model_time"]), now, now
                if not state["active"]:
                    finished = state["stop_reason"] == "finished"
                    if not finished:
                        print(f"\nСимуляцию остановили ({state['stop_reason']}) — выхожу.")
                        break
            estimate = model + timedelta(seconds=(now - synced) * speed) if model else None
            if finished:
                estimate = datetime.combine(day, datetime.max.time())
            due = []
            if finished or now - last_send >= SEND_SECONDS:
                while i < len(events) and estimate and events[i]["ts"] <= estimate and len(due) < BATCH_MAX:
                    due.append(payload(events[i]))
                    i += 1
            if due:
                # Полная пачка — значит, есть отставание: следующую отправляем сразу
                last_send = 0.0 if len(due) == BATCH_MAX else now
                res = api.call("POST", "/api/ingest/events", {"events": due}, timeout=120)
                sent += res["received"]
                accepted += res["accepted"]
                duplicates += res["duplicates"]
                skipped += res["skipped_unknown_channel"] + res["skipped_invalid"]
            if now - last_print >= PRINT_SECONDS or (finished and i >= len(events)):
                last_print = now
                shown = min(estimate, datetime.combine(day, datetime.max.time())) if estimate else None
                print(
                    f"\r[{shown:%d.%m %H:%M}] ×{speed} · отправлено {sent} из {len(events)} "
                    f"(новых {accepted}, дублей {duplicates}, пропущено {skipped}) · "
                    f"критических уведомлений {state['critical_sent']}   ",
                    end="", flush=True,
                )
            if finished and i >= len(events):
                break
            if not due:
                time.sleep(0.25)
    except KeyboardInterrupt:
        print("\nCtrl+C — останавливаю симуляцию…")
        try:
            api.call("POST", "/api/sim/stop")
        except ApiError as e:
            print(f"Не удалось остановить: {e.detail}", file=sys.stderr)
        return 130
    except ApiError as e:
        print(f"\nОшибка API: {e.detail}. Останавливаю симуляцию.", file=sys.stderr)
        try:
            api.call("POST", "/api/sim/stop")
        except ApiError:
            pass
        return 1
    state = api.call("POST", "/api/sim/stop") if not finished else api.call("GET", "/api/sim/state")
    print(
        f"\nГотово: сутки {day:%d.%m.%Y} проиграны. Событий отправлено {sent}: новых {accepted}, "
        f"дублей {duplicates}, пропущено {skipped}. Критических уведомлений: {state['critical_sent']}."
    )
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Проигрывание суток журнала в /api/ingest/events с ускорением")
    ap.add_argument("--day", type=date.fromisoformat, help="Сутки YYYY-MM-DD; по умолчанию последние полные с прогнозами")
    ap.add_argument("--speed", type=int, default=60, help="Ускорение ×1…×600 (по умолчанию 60: сутки за 24 минуты)")
    ap.add_argument("--api", default=os.environ.get("SIM_API_URL", "http://localhost:8000"))
    ap.add_argument("--user", default=os.environ.get("SIM_USERNAME", "engineer"))
    ap.add_argument("--password", default=os.environ.get("SIM_PASSWORD", "engineer123"),
                    help="По умолчанию — пароль демо-инженера; в production задайте SIM_PASSWORD")
    ap.add_argument("--file", help="CSV журнала вместо events_recent (заголовки как в файлах заказчика)")
    ap.add_argument("--purge-day", action="store_true",
                    help="Удалить события этих суток из events_recent перед проигрышем (нужен --yes; копия — в CSV)")
    ap.add_argument("--yes", action="store_true", help="Подтверждение для --purge-day")
    args = ap.parse_args()
    if not 1 <= args.speed <= 600:
        ap.error("--speed: от 1 до 600")
    sys.exit(run(args))


if __name__ == "__main__":
    main()
