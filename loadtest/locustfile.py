"""Нагрузочная проверка (ТЗ, раздел 11: не менее 20 одновременных пользователей без деградации).

Виртуальные пользователи разных ролей проходят сценарий диспетчера: вход, дашборд, схема объектов, журнал
с фильтрами, карточка датчика с рекомендацией, заявки и «Рекомендованные работы», поток уведомлений SSE.
В режиме WRITE=1 диспетчеры и инженеры ещё отмечают решения и создают черновики заявок (черновики удаляются
сразу) — только для изолированного стека. WRITE=0 — только чтение (для стека с реальными данными).

Запуск (Docker, из корня репозитория) — docs/LOADTEST.md:
  docker run --rm -v "$PWD/loadtest:/mnt/locust" -e LOAD_AT=2026-08-01T12:00:00 -e WRITE=1 locustio/locust:2.43.1 \
    -f /mnt/locust/locustfile.py --headless -u 30 -r 5 -t 3m --host http://host.docker.internal:19180 \
    --csv /mnt/locust/results/recs --only-summary
"""

import itertools
import os
import random
import time

from locust import HttpUser, between, events, task

AT = os.environ.get("LOAD_AT", "2026-08-01T12:00:00")
WRITE = os.environ.get("WRITE", "0") == "1"
PASSWORDS = {
    "dispatcher": os.environ.get("PASS_DISPATCHER", "dispatcher123"),
    "engineer": os.environ.get("PASS_ENGINEER", "engineer123"),
    "manager": os.environ.get("PASS_MANAGER", "manager123"),
    "admin": os.environ.get("PASS_ADMIN", "admin123"),
}
# Доли ролей: в смене больше всего диспетчеров
ROLES = itertools.cycle(["dispatcher", "dispatcher", "engineer", "dispatcher", "manager", "admin", "dispatcher", "engineer"])
WRITERS = {"dispatcher", "engineer", "admin"}


class Dispatcher(HttpUser):
    # Пауза «на подумать» между действиями человека
    wait_time = between(1, 3)

    def on_start(self):
        self.role = next(ROLES)
        for _ in range(10):
            with self.client.post("/api/auth/login", json={"username": self.role, "password": PASSWORDS[self.role]},
                                  name="POST /api/auth/login", catch_response=True) as r:
                if r.status_code != 429:
                    break
                # Лимит входов с одного адреса: ждём, как попросил сервер, — это не ошибка сервиса
                r.success()
                time.sleep(float(r.headers.get("Retry-After", 5)))
        r.raise_for_status()
        self.client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        self.predictions: list[dict] = []
        self.dashboard()

    # ---------- чтение ----------

    @task(4)
    def dashboard(self):
        self.client.get("/api/dashboard/summary", params={"at": AT}, name="GET /api/dashboard/summary")
        r = self.client.get("/api/predictions", params={"at": AT, "limit": 20}, name="GET /api/predictions (топ-20)")
        if r.ok:
            self.predictions = r.json()["items"] or self.predictions
        self.client.get("/api/notifications", params={"at": AT}, name="GET /api/notifications")

    @task(2)
    def objects(self):
        self.client.get("/api/objects", params={"at": AT}, name="GET /api/objects")

    @task(3)
    def journal(self):
        level = random.choice(["critical", "risk", None])
        params = {"at": AT, "limit": 50, **({"risk_level": level} if level else {})}
        self.client.get("/api/journal", params=params, name="GET /api/journal (фильтры)")
        if random.random() < 0.3:
            self.client.get("/api/journal", params={"at": AT, "limit": 50, "decision": "none", "outcome": "happened"},
                            name="GET /api/journal (фильтры)")

    @task(4)
    def channel_card(self):
        if not self.predictions:
            return
        p = random.choice(self.predictions)
        cid, pid = p["channel"]["id"], p["prediction_id"]
        self.client.get(f"/api/channels/{cid}", params={"at": AT}, name="GET /api/channels/{id}")
        self.client.get(f"/api/channels/{cid}/history", params={"at": AT, "days": 30, "granularity": "day"},
                        name="GET /api/channels/{id}/history")
        self.client.get(f"/api/predictions/{pid}/recommendation", name="GET /api/predictions/{id}/recommendation")

    @task(1)
    def work_orders(self):
        self.client.get("/api/work-orders", params={"at": AT, "status": ["draft", "submitted", "in_progress"]},
                        name="GET /api/work-orders")
        if random.random() < 0.5:
            self.client.get("/api/maintenance/plan", params={"at": AT, "limit": 20}, name="GET /api/maintenance/plan")

    @task(1)
    def sse(self):
        """Тикет → поток уведомлений до первого события snapshot (как при открытии интерфейса)."""
        r = self.client.post("/api/notifications/ticket", name="POST /api/notifications/ticket")
        if not r.ok:
            return
        start = time.perf_counter()
        with self.client.get("/api/notifications/stream", params={"ticket": r.json()["ticket"], "at": AT}, stream=True,
                             name="GET /api/notifications/stream (до snapshot)", catch_response=True, timeout=20) as s:
            got = False
            for line in s.iter_lines():
                if line.startswith(b"event: snapshot"):
                    got = True
                    break
            if got:
                s.success()
            else:
                s.failure("нет события snapshot")
        events.request.fire(request_type="SSE", name="snapshot получен за", response_time=(time.perf_counter() - start) * 1000,
                            response_length=0, exception=None if got else RuntimeError("нет snapshot"), context={})

    # ---------- запись (только WRITE=1) ----------

    @task(1)
    def decide_and_draft(self):
        if not WRITE or self.role not in WRITERS or not self.predictions:
            return
        p = random.choice(self.predictions)
        reasons = self.client.get("/api/reasons", params={"decision_type": "monitor"}, name="GET /api/reasons").json()
        self.client.post(f"/api/predictions/{p['prediction_id']}/decision", params={"at": AT},
                         json={"decision_type": "monitor", "reason_id": reasons[0]["id"], "comment": "нагрузочный тест"},
                         name="POST /api/predictions/{id}/decision")
        r = self.client.post("/api/work-orders", params={"at": AT}, json={"prediction_id": p["prediction_id"]},
                             name="POST /api/work-orders")
        if r.status_code == 201:
            self.client.delete(f"/api/work-orders/{r.json()['id']}", name="DELETE /api/work-orders/{id}")
