"""Смоделированный журнал решений диспетчера по прошлым прогнозам (docs/SIMULATED_DECISIONS.md).

Журнала ОДС и решений диспетчеров у заказчика нет (ответ организаторов 23.09): решения предлагается моделировать
правдоподобно, «а не просто случайными числами». Модель поведения (прозрачная, параметры ниже):

- Диспетчер разбирает уведомления — новые переходы в «Критично» (как колокольчик и SSE) и часть «Риска»;
  не каждое: днём разбирает 92 %, ночью (0–7 ч) 80 %.
- Задержка реакции — логнормальная: медиана 12 мин днём, 25 мин ночью, не больше 3 ч.
- Решение зависит от того, чем закончилось (исход известен задним числом) и от вероятности прогноза:
  сбывшийся — чаще «Выезд» (65–85 % в зависимости от вероятности), иначе «Наблюдение», редко «Ложное»;
  несбывшийся — «Ложное срабатывание» или «Наблюдение», изредка «Выезд» (диспетчер не знает будущего).
- Причина — из справочника по виду события: для газа в будни 9–14 «ложное» — «Проводятся плановые работы»,
  для потери связи — «Пропадание связи» / «Сбой канала связи», и т. д.; комментарий — «смоделировано».
- Каждое решение помечено source = simulation; интерфейс показывает бейдж «смоделировано».

Запуск — только явно, не при старте и не в production (без --force):
    docker compose exec api python -m scripts.simulate_decisions --from 2026-06-01 --to 2026-06-30
Повторный запуск за тот же период сначала удаляет прежние смоделированные решения (решения людей не трогает).
"""

import argparse
import math
import random
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SEED = 20260926
REVIEW_DAY, REVIEW_NIGHT = 0.92, 0.80
RISK_SHARE = 0.15  # доля «Риска» (не критичных), которые диспетчер тоже разбирает
DELAY_MEDIAN_MIN = {"day": 12.0, "night": 25.0}
DELAY_SIGMA, DELAY_MAX_MIN = 0.8, 180.0


def decide(rng: random.Random, happened: bool | None, prob: float) -> str:
    if happened:
        p_dispatch = 0.65 + 0.2 * min(1.0, max(0.0, (prob - 0.35) / 0.6))
        return rng.choices(["dispatch", "monitor", "false_alarm"], [p_dispatch, 0.95 - p_dispatch, 0.05])[0]
    if happened is False:
        return rng.choices(["false_alarm", "monitor", "dispatch"], [0.5, 0.38, 0.12])[0]
    return rng.choices(["monitor", "dispatch"], [0.7, 0.3])[0]  # исход ещё неизвестен


def reason_code(rng: random.Random, decision: str, sensor_type: str, at: datetime, likely_kind: str | None) -> str:
    gas_check = "газ" in sensor_type.lower() and at.weekday() < 5 and 9 <= at.hour < 14
    if decision == "dispatch":
        if likely_kind == "link":
            return "link_loss"
        if sensor_type in ("Состояние фазы", "ИБП"):
            return "power_loss"
        if sensor_type == "Датчик затопления":
            return "flood_risk"
        return rng.choices(["model_risk_confirmed", "repeated_fault_msgs"], [0.6, 0.4])[0]
    if decision == "false_alarm":
        if gas_check:
            return "planned_works"
        if likely_kind == "link":
            return "comm_glitch"
        if sensor_type in ("Датчик движения", "КД Дверь", "КД Люк", "КД АВ"):
            return rng.choices(["staff_pass", "known_interference"], [0.7, 0.3])[0]
        return rng.choices(["known_interference", "comm_glitch", "already_replaced"], [0.5, 0.35, 0.15])[0]
    return rng.choices(["need_more_data", "single_spike", "wait_related_service"], [0.5, 0.35, 0.15])[0]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Смоделированный журнал решений диспетчера")
    ap.add_argument("--from", dest="date_from", type=date.fromisoformat, required=True)
    ap.add_argument("--to", dest="date_to", type=date.fromisoformat, required=True)
    ap.add_argument("--force", action="store_true", help="разрешить в production")
    args = ap.parse_args()

    from app.config import get_settings
    from app.db import SessionLocal
    from app.labels import likely_kind
    from app.models import Decision, Prediction, Reason, User, UserRole
    from app.notifications import critical_transitions

    if get_settings().is_production and not args.force:
        print("APP_ENV=production: смоделированные решения — только для демо и среза (--force, если нужно)", file=sys.stderr)
        return 1
    start = datetime.combine(args.date_from, time.min)
    end = datetime.combine(args.date_to, time.max)
    rng = random.Random(SEED)
    with SessionLocal() as db:
        reasons = {r.code: r.id for r in db.scalars(select(Reason))}
        deciders = list(db.scalars(
            select(User.id).join(UserRole, UserRole.user_id == User.id)
            .where(UserRole.role.in_(("dispatcher_ods", "dispatcher")), User.is_active.is_(True)).order_by(User.id)
        ).unique()) or [None]
        removed = db.execute(delete(Decision).where(
            Decision.source == "simulation",
            Decision.prediction_id.in_(select(Prediction.id).where(Prediction.at.between(start, end))))).rowcount
        # Уведомления, которые видел диспетчер: новые переходы в «Критично» и часть «Риска» тех же срезов
        crit = critical_transitions(db, start, end)
        risk_ids = list(db.scalars(select(Prediction.id).where(
            Prediction.at.between(start, end), Prediction.risk_level == "risk")))
        rng.shuffle(risk_ids)
        ids = [t.prediction_id for t in crit] + risk_ids[: int(len(crit) * RISK_SHARE)]
        stats: Counter = Counter()
        for chunk in range(0, len(ids), 2000):
            preds = db.scalars(select(Prediction).where(Prediction.id.in_(ids[chunk : chunk + 2000]))).all()
            for p in sorted(preds, key=lambda x: x.id):
                night = p.at.hour < 7
                if rng.random() > (REVIEW_NIGHT if night else REVIEW_DAY):
                    stats["не разобрано"] += 1
                    continue
                median = DELAY_MEDIAN_MIN["night" if night else "day"]
                delay = min(DELAY_MAX_MIN, math.exp(rng.gauss(math.log(median), DELAY_SIGMA)))
                happened = p.outcome.happened if p.outcome is not None else None
                d = decide(rng, happened, p.prob)
                lk = likely_kind(p.kind_probs)
                code = reason_code(rng, d, p.channel.sensor_type, p.at, lk[0] if lk else None)
                db.add(Decision(prediction_id=p.id, decision_type=d, reason_id=reasons.get(code, reasons.get("other")),
                                comment="Смоделировано: журнала ОДС нет (source=simulation)",
                                user_id=rng.choice(deciders), created_at=p.at + timedelta(minutes=delay),
                                source="simulation"))
                stats[(d, happened)] += 1
            db.flush()
        db.commit()
    total = sum(v for k, v in stats.items() if isinstance(k, tuple))
    print(f"Период {args.date_from} — {args.date_to}: уведомлений {len(ids)}, смоделировано решений {total} "
          f"(удалено прежних смоделированных: {removed}), не разобрано {stats['не разобрано']}")
    for (d, h), n in sorted(((k, v) for k, v in stats.items() if isinstance(k, tuple)), key=lambda kv: -kv[1]):
        print(f"  {d:<12} исход {'сбылся' if h else 'не сбылся' if h is False else 'неизвестен':<10} {n:>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
