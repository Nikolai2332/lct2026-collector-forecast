"""Замер задержки потоковых данных (ТЗ, разделы 9 и 11: не более 300 секунд).

  python loadtest/latency.py [адрес] [--at 2026-08-01T12:00:00] [--day 2026-08-01] [--speed 600] [-n 20]

1. Поток событий: POST /api/ingest/events (одно событие) → событие видно в карточке датчика (last_event).
   Пишет события в базу — только для изолированного стека (по умолчанию http://localhost:19100).
2. Уведомления: симуляция суток (прогнозы рассчитаны заранее) → от момента, когда модельные часы перешли в новый
   срез прогнозов, до прихода событий critical_prediction / sim_clock в SSE.
Только стандартная библиотека Python.
"""

import argparse
import json
import statistics
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

p = argparse.ArgumentParser()
p.add_argument("base", nargs="?", default="http://127.0.0.1:19100")
p.add_argument("--user", default="engineer")
p.add_argument("--password", default="engineer123")
p.add_argument("--at", default="2026-08-01T12:00:00", help="момент для выбора датчика")
p.add_argument("--event-ts", default="2026-09-01T00:00:00", help="время первого тестового события (после конца данных)")
p.add_argument("--day", default="2026-08-01", help="сутки симуляции")
p.add_argument("--speed", type=int, default=600)
p.add_argument("--slices", type=int, default=3, help="сколько смен среза ждать в симуляции")
p.add_argument("-n", type=int, default=20, help="сколько событий отправить")
args = p.parse_args()
BASE = args.base.rstrip("/")


def call(method: str, path: str, body=None, token: str | None = None, timeout: float = 30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def show(title: str, values: list[float], unit: str = "мс") -> None:
    print(f"  {title}: медиана {statistics.median(values):.0f} {unit}, p95 {pct(values, 0.95):.0f} {unit}, "
          f"максимум {max(values):.0f} {unit} (n={len(values)})")


token = call("POST", "/api/auth/login", {"username": args.user, "password": args.password})["access_token"]

# ---------- 1. Поток событий → карточка датчика ----------
top = call("GET", f"/api/predictions?at={args.at}&limit=1", token=token)["items"][0]
cid = top["channel"]["id"]
print(f"1. POST /api/ingest/events → событие в карточке датчика (канал {cid})")
post_ms, visible_ms = [], []
t0 = datetime.fromisoformat(args.event_ts) + timedelta(seconds=int(time.time()) % 3600)  # новые секунды при повторе
for i in range(args.n):
    ts = (t0 + timedelta(seconds=i)).isoformat()
    start = time.perf_counter()
    res = call("POST", "/api/ingest/events", {"events": [{"channel_id": cid, "ts": ts, "is_alarm": False, "value": "Норма"}]},
               token=token)
    post_ms.append((time.perf_counter() - start) * 1000)
    assert res["accepted"] == 1, res
    while True:
        card = call("GET", f"/api/channels/{cid}?at={ts}", token=token)
        if card["last_event"] and card["last_event"]["ts"] == ts:
            break
        if time.perf_counter() - start > 300:
            raise SystemExit("событие не появилось за 300 с")
        time.sleep(0.05)
    visible_ms.append((time.perf_counter() - start) * 1000)
show("ответ приёма", post_ms)
show("от отправки до карточки", visible_ms)

# ---------- 2. Симуляция: смена среза → уведомление в SSE ----------
state = call("GET", "/api/sim/state", token=token)
if not state["enabled"]:
    raise SystemExit("Симуляция выключена (SIM_ENABLED=false) — замер 2 пропущен")
if state["active"]:
    call("POST", "/api/sim/stop", token=token)
print(f"2. Симуляция {args.day} ×{args.speed}: смена среза → SSE (ждём {args.slices} смены)")
ticket = call("POST", "/api/notifications/ticket", token=token)["ticket"]
received: list[tuple[float, str, dict]] = []
stop = threading.Event()


def reader():
    req = urllib.request.Request(f"{BASE}/api/notifications/stream?ticket={ticket}")
    with urllib.request.urlopen(req, timeout=600) as r:
        event = None
        for raw in r:
            line = raw.decode().rstrip("\n")
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event:
                received.append((time.time(), event, json.loads(line[6:]) if line[6:].startswith(("{", "[")) else {}))
            if stop.is_set():
                return


threading.Thread(target=reader, daemon=True).start()
time.sleep(1)
t_start = time.time()
call("POST", "/api/sim/start", {"day": args.day, "speed": args.speed}, token=token)
day0 = datetime.fromisoformat(args.day)
deadline = time.time() + 30 + args.slices * 6 * 3600 / args.speed * 1.5
server_delay, client_delay, criticals = [], [], 0
seen = set()
try:
    while time.time() < deadline and len(seen) < args.slices + 1:
        time.sleep(0.2)
        for t, event, data in list(received):
            if event == "critical_prediction":
                continue
            if event != "sim_clock" or not data.get("snapshot_at") or data["snapshot_at"] in seen or not data.get("active"):
                continue
            seen.add(data["snapshot_at"])
            snap = datetime.fromisoformat(data["snapshot_at"])
            model = datetime.fromisoformat(data["model_time"])
            server_delay.append((model - snap).total_seconds() / args.speed * 1000)
            client_delay.append((t - (t_start + (snap - day0).total_seconds() / args.speed)) * 1000)
    criticals = sum(1 for _, e, _ in received if e == "critical_prediction")
finally:
    stop.set()
    call("POST", "/api/sim/stop", token=token)
# Первый срез (00:00) наступает в момент запуска — его задержка включает сам запуск; считаем со второго
show("сервер: переход часов в новый срез → публикация в SSE", server_delay[1:] or server_delay)
show("клиент: переход в новый срез → событие в браузере (оценка)", client_delay[1:] or client_delay)
print(f"  уведомлений critical_prediction за это время: {criticals}; срезов: {len(seen)}")
print("Цель ТЗ — не более 300 000 мс (300 с)")
