"""Отметки отказов на графике карточки датчика: все 3 вида из разметки ML + статусы из событий."""

from datetime import datetime

import pytest
from sqlalchemy import delete

from app.models import ChannelFault

CHANNEL = 334609  # в демо-сиде: «Неисправен» 2026-08-01 18:42:10 (событие)
EVENT_FAULT = datetime(2026, 8, 1, 18, 42, 10)


@pytest.fixture
def ml_faults(session_factory):
    rows = [
        ChannelFault(channel_id=CHANNEL, ts=datetime(2026, 8, 1, 9, 15), kind="Пропадание связи"),
        ChannelFault(channel_id=CHANNEL, ts=datetime(2026, 8, 1, 10, 0), kind="Отключено устройство"),
        # тот же отказ, что и в событиях, — на графике должен быть один раз
        ChannelFault(channel_id=CHANNEL, ts=EVENT_FAULT, kind="Неисправен"),
        # вне периода запроса
        ChannelFault(channel_id=CHANNEL, ts=datetime(2026, 7, 20, 3, 0), kind="Пропадание связи"),
    ]
    with session_factory() as s:
        s.add_all(rows)
        s.commit()
    yield
    with session_factory() as s:
        s.execute(delete(ChannelFault).where(ChannelFault.channel_id == CHANNEL))
        s.commit()


def test_history_marks_all_fault_kinds(client, auth, ml_faults):
    body = client.get(f"/api/channels/{CHANNEL}/history", params={"at": "2026-08-02T00:00:00", "days": 1},
                      headers=auth()).json()
    marks = [(f["ts"], f["kind"]) for f in body["faults"]]
    assert ("2026-08-01T09:15:00", "Пропадание связи") in marks
    assert ("2026-08-01T10:00:00", "Отключено устройство") in marks
    assert marks.count(("2026-08-01T18:42:10", "Неисправен")) == 1
    assert all(ts > "2026-08-01T00:00:00" for ts, _ in marks), "отказы вне периода не отдаются"
    assert [ts for ts, _ in marks] == sorted(ts for ts, _ in marks)


def test_history_hourly_has_ml_faults(client, auth, ml_faults):
    body = client.get(f"/api/channels/{CHANNEL}/history",
                      params={"at": "2026-08-02T00:00:00", "days": 1, "granularity": "hour"}, headers=auth()).json()
    assert {"Пропадание связи", "Отключено устройство", "Неисправен"} <= {f["kind"] for f in body["faults"]}
