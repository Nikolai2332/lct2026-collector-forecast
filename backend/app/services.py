"""Общие запросы: срез прогнозов на момент at, дерево объектов, сборка ответов."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app import schemas
from app.access import Access
from app.factors_human import humanize
from app.labels import DECISION_LABELS, WORK_ORDER_PRIORITY_LABELS, WORK_ORDER_STATUS_LABELS, likely_kind
from app.models import Channel, Decision, ModelMetric, ModelThreshold, Object, Prediction, WorkOrder
from app.timeutil import sim_moment
from app.risk import level_of


# ---------- Дерево объектов ----------


@dataclass
class ObjectInfo:
    id: int
    name: str
    level: int
    kind: str
    parent_id: int | None
    children: list[int] = field(default_factory=list)


class ObjectIndex:
    """Всё дерево в памяти: объектов меньше сотни, так дешевле, чем рекурсивные запросы."""

    def __init__(self, rows: Iterable[Object], access: Access):
        self.nodes = {o.id: ObjectInfo(o.id, o.name, o.level, o.kind, o.parent_id) for o in rows}
        for node in sorted(self.nodes.values(), key=lambda n: n.id):
            if node.parent_id in self.nodes:
                self.nodes[node.parent_id].children.append(node.id)
        self._paths: dict[int, list[str]] = {}
        # Область видимости (app.access): объекты, к которым привязаны видимые датчики, — поддеревья узлов области.
        # Их предки остаются в дереве как «папки» (путь «район → объект»), но их собственные датчики не видны.
        # None — видно всё
        self.scope_key = access.key
        self.channel_objects: frozenset[int] | None = None
        if not access.unrestricted:
            total = len(self.nodes)
            subtree: set[int] = set()
            for root in access.scope_ids:
                if root in self.nodes:
                    subtree.update(self._subtree(root))
            visible = set(subtree)
            for oid in list(subtree):
                cur = self.nodes[oid].parent_id
                while cur is not None and cur in self.nodes and cur not in visible:
                    visible.add(cur)
                    cur = self.nodes[cur].parent_id
            for oid in list(self.nodes):
                if oid not in visible:
                    del self.nodes[oid]
            for node in self.nodes.values():
                node.children = [c for c in node.children if c in visible]
            if len(subtree) < len(visible) or len(visible) < total:
                self.channel_objects = frozenset(subtree)
            else:
                # Область покрывает всё дерево (в данных заказчика район один — диспетчер района видит всё):
                # тот же ключ кэша и те же запросы, что у ОДС, без лишнего IN (…)
                self.scope_key = None

    @classmethod
    def load(cls, db: Session, access: Access) -> "ObjectIndex":
        """Дерево объектов в пределах области пользователя. access обязателен: забытая область не должна
        молча превращаться в «видно всё»; где нужна полная картина — FULL_ACCESS явно."""
        return cls(db.scalars(select(Object)), access)

    def _subtree(self, object_id: int) -> list[int]:
        result, stack = [], [object_id]
        while stack:
            cur = stack.pop()
            result.append(cur)
            stack.extend(self.nodes[cur].children)
        return result

    def channel_visible(self, object_id: int) -> bool:
        """Датчик объекта object_id в области пользователя."""
        return self.channel_objects is None or object_id in self.channel_objects

    def roots(self) -> list[ObjectInfo]:
        return [n for n in sorted(self.nodes.values(), key=lambda n: n.id) if n.parent_id not in self.nodes]

    def path(self, object_id: int) -> list[str]:
        if object_id not in self._paths:
            names, cur, seen = [], self.nodes.get(object_id), set()
            while cur is not None and cur.id not in seen:
                seen.add(cur.id)
                names.append(cur.name)
                cur = self.nodes.get(cur.parent_id) if cur.parent_id is not None else None
            self._paths[object_id] = names[::-1]
        return self._paths[object_id]

    def ref(self, object_id: int) -> schemas.ObjectRef:
        node = self.nodes.get(object_id)
        return schemas.ObjectRef(id=object_id, name=node.name if node else "—", path=self.path(object_id))

    def subtree_ids(self, object_id: int) -> list[int]:
        if object_id not in self.nodes:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объект не найден")
        return self._subtree(object_id)


# ---------- Срез прогнозов ----------


def snapshot_at(db: Session, at: datetime) -> datetime | None:
    """Последний момент расчёта прогнозов не позже at. Использует индекс ix_predictions_at_prob."""
    return db.scalar(select(func.max(Prediction.at)).where(Prediction.at <= at))


def current_model_version(db: Session) -> str | None:
    version = db.scalar(select(ModelThreshold.model_version).where(ModelThreshold.is_selected).limit(1))
    if version is None:
        version = db.scalar(select(ModelMetric.model_version).order_by(ModelMetric.computed_at.desc()).limit(1))
    if version is None:
        version = db.scalar(select(Prediction.model_version).order_by(Prediction.at.desc()).limit(1))
    return version


def working_threshold(db: Session) -> float:
    """Рабочий порог модели; 0.5 — пока ML не прислал таблицу порогов."""
    value = db.scalar(select(ModelThreshold.threshold).where(ModelThreshold.is_selected).limit(1))
    return float(value) if value is not None else 0.5


def apply_channel_filters(
    stmt: Select,
    idx: ObjectIndex,
    object_id: int | None = None,
    system_type: str | None = None,
    sensor_type: str | None = None,
    q: str | None = None,
) -> Select:
    """Фильтры по каналу. Запрос уже должен содержать Channel во FROM/JOIN."""
    if idx.channel_objects is not None:  # область видимости — всегда, даже без фильтров
        stmt = stmt.where(Channel.object_id.in_(sorted(idx.channel_objects)))
    if object_id is not None:
        stmt = stmt.where(Channel.object_id.in_(idx.subtree_ids(object_id)))
    if system_type:
        stmt = stmt.where(Channel.system_type == system_type)
    if sensor_type:
        stmt = stmt.where(Channel.sensor_type == sensor_type)
    if q:
        pattern = f"%{q.strip()}%"
        # DIALECT: ilike → ILIKE в PostgreSQL, lower() LIKE lower() в SQLite. Для 11 тыс. каналов индекс не нужен;
        # если понадобится — pg_trgm GIN по channels.name.
        conditions = [Channel.name.ilike(pattern), Channel.tag.ilike(pattern)]
        if q.strip().isdigit():
            conditions.append(Channel.id == int(q.strip()))
        stmt = stmt.where(or_(*conditions))
    return stmt


def restrict_by_channel(stmt: Select, column, idx: ObjectIndex) -> Select:
    """Область видимости для запросов без Channel в FROM: column — channel_id (прогнозы, отказы, события)."""
    if idx.channel_objects is None:
        return stmt
    return stmt.where(column.in_(select(Channel.id).where(Channel.object_id.in_(sorted(idx.channel_objects)))))


# ---------- Решения и заявки к прогнозам ----------


def latest_decisions(db: Session, prediction_ids: Sequence[int], at: datetime | None = None) -> dict[int, Decision]:
    if not prediction_ids:
        return {}
    stmt = select(Decision).where(Decision.prediction_id.in_(prediction_ids))
    if at is not None:
        stmt = stmt.where(Decision.created_at <= at)
    result: dict[int, Decision] = {}
    # Актуальное решение — последнее по id (так же считает журнал через max(id))
    for d in db.scalars(stmt.order_by(Decision.id)):
        result[d.prediction_id] = d
    return result


def work_orders_by_prediction(
    db: Session, prediction_ids: Sequence[int], at: datetime | None = None
) -> dict[int, tuple[int, str, str]]:
    """Последняя неотменённая заявка по каждому прогнозу: (id, статус, номер)."""
    if not prediction_ids:
        return {}
    stmt = select(WorkOrder.prediction_id, WorkOrder.id, WorkOrder.status, WorkOrder.number).where(
        WorkOrder.prediction_id.in_(prediction_ids), WorkOrder.status != "cancelled"
    )
    if at is not None:
        stmt = stmt.where(WorkOrder.created_at <= at)
    result: dict[int, tuple[int, str, str]] = {}
    for pid, wid, st, number in db.execute(stmt.order_by(WorkOrder.id)):
        result[pid] = (wid, st, number)
    return result


# ---------- Сборка ответов ----------


def channel_ref(ch: Channel) -> schemas.ChannelRef:
    return schemas.ChannelRef(id=ch.id, name=ch.name, sensor_type=ch.sensor_type, system_type=ch.system_type)


def factor_phrases(top_factors) -> list[str]:
    """top_factors от ML — список {feature, value, norm, phrase}; допускаем и просто список фраз."""
    phrases = []
    for f in top_factors or []:
        if isinstance(f, str):
            phrases.append(f)
        elif isinstance(f, dict) and f.get("phrase"):
            phrases.append(str(f["phrase"]))
    return phrases[:3]


def _kind_fields(kind_probs) -> dict:
    lk = likely_kind(kind_probs)
    return {"kind_probs": kind_probs or None, "likely_kind": lk[0] if lk else None,
            "likely_kind_label": lk[1] if lk else None}


def factors_human(top_factors) -> list[schemas.FactorHuman]:
    return [schemas.FactorHuman(text=h.text, short=h.short, features=h.features, tech=h.tech) for h in humanize(top_factors)]


def factor_details(top_factors) -> list[schemas.FactorDetail]:
    details = []
    for f in top_factors or []:
        if isinstance(f, str):
            details.append(schemas.FactorDetail(feature="", phrase=f))
        elif isinstance(f, dict):
            details.append(
                schemas.FactorDetail(
                    feature=str(f.get("feature", "")),
                    value=f.get("value"),
                    norm=f.get("norm"),
                    phrase=str(f.get("phrase", "")),
                )
            )
    return details


def decision_out(d: Decision) -> schemas.DecisionOut:
    return schemas.DecisionOut(
        id=d.id,
        decision_type=d.decision_type,
        decision_label=DECISION_LABELS[d.decision_type],
        reason=schemas.ReasonRef(id=d.reason.id, name=d.reason.name),
        comment=d.comment,
        user=schemas.UserRef(id=d.user.id, username=d.user.username, full_name=d.user.full_name) if d.user else None,
        created_at=d.created_at,
        source=d.source or "user",
    )


def outcome_out(p: Prediction) -> schemas.Outcome | None:
    if p.outcome is None:
        return None
    # Симуляция: по модельному времени горизонт ещё не закрылся — исход «пока неизвестен», будущее не показываем
    moment = sim_moment.get()
    if moment is not None and p.at + timedelta(hours=p.horizon_h) > moment:
        return None
    return schemas.Outcome(happened=p.outcome.happened, fault_at=p.outcome.fault_at)


def prediction_items(
    db: Session, preds: Sequence[Prediction], idx: ObjectIndex, at: datetime | None
) -> list[schemas.PredictionItem]:
    ids = [p.id for p in preds]
    decisions = latest_decisions(db, ids, at)
    orders = work_orders_by_prediction(db, ids, at)
    items = []
    for p in preds:
        d = decisions.get(p.id)
        wo = orders.get(p.id)
        items.append(
            schemas.PredictionItem(
                prediction_id=p.id,
                channel=channel_ref(p.channel),
                object=idx.ref(p.channel.object_id),
                prob=round(p.prob, 4),
                health=p.health,
                risk_level=level_of(p),
                factors=factor_phrases(p.top_factors),
                factors_human=factors_human(p.top_factors),
                **_kind_fields(p.kind_probs),
                outcome=outcome_out(p),
                decision=decision_out(d) if d else None,
                work_order_id=wo[0] if wo else None,
            )
        )
    return items


def prediction_detail(db: Session, p: Prediction, idx: ObjectIndex, at: datetime | None) -> schemas.PredictionDetail:
    item = prediction_items(db, [p], idx, at)[0]
    stmt = select(Decision).where(Decision.prediction_id == p.id)
    if at is not None:
        stmt = stmt.where(Decision.created_at <= at)
    history = db.scalars(stmt.order_by(Decision.id.desc())).all()
    return schemas.PredictionDetail(
        **item.model_dump(),
        at=p.at,
        horizon_h=p.horizon_h,
        model_version=p.model_version,
        factors_detail=factor_details(p.top_factors),
        decisions=[decision_out(d) for d in history],
    )


def work_order_out(wo: WorkOrder, idx: ObjectIndex) -> schemas.WorkOrderOut:
    return schemas.WorkOrderOut(
        id=wo.id,
        number=wo.number,
        status=wo.status,
        status_label=WORK_ORDER_STATUS_LABELS[wo.status],
        priority=wo.priority,
        priority_label=WORK_ORDER_PRIORITY_LABELS[wo.priority],
        prediction_id=wo.prediction_id,
        channel=channel_ref(wo.channel),
        object=idx.ref(wo.object_id),
        reason=schemas.ReasonRef(id=wo.reason.id, name=wo.reason.name) if wo.reason else None,
        recommendation=(
            schemas.RecommendationRef(id=wo.recommendation.id, text=wo.recommendation.text)
            if wo.recommendation
            else None
        ),
        recommendation_text=wo.recommendation_text,
        description=wo.description,
        assignee=wo.assignee,
        due_at=wo.due_at,
        created_by=(
            schemas.UserRef(id=wo.author.id, username=wo.author.username, full_name=wo.author.full_name)
            if wo.author
            else None
        ),
        created_at=wo.created_at,
        updated_at=wo.updated_at,
        closed_at=wo.closed_at,
    )


def get_or_404(db: Session, model, obj_id, message: str):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, message)
    return obj
