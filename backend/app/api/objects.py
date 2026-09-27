from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app import schemas
from app.access import Access, get_access
from app.api.channels import channel_page
from app.db import get_db
from app.deps import Page, at_param, page_param
from app.labels import RISK_ORDER
from app.models import Channel, Prediction
from app.risk import level_expr
from app.security import get_current_user
from app.services import ObjectIndex, apply_channel_filters, snapshot_at

router = APIRouter(prefix="/api/objects", tags=["Объекты"], dependencies=[Depends(get_current_user)])

LEVELS = ("normal", "attention", "risk", "critical")


def _direct_counts(db: Session, idx: ObjectIndex, snap, system_type, sensor_type) -> dict[int, dict]:
    """Счётчики по уровням риска у датчиков, привязанных к объекту напрямую."""
    level_col = level_expr()
    stmt = (
        select(Channel.object_id, level_col, func.count())
        .select_from(Channel)
        .outerjoin(Prediction, and_(Prediction.channel_id == Channel.id, Prediction.at == snap))
        .group_by(Channel.object_id, level_col)
    )
    stmt = apply_channel_filters(stmt, idx, system_type=system_type, sensor_type=sensor_type)
    counts: dict[int, dict] = defaultdict(lambda: {"channels": 0, **{lvl: 0 for lvl in LEVELS}})
    for object_id, level, n in db.execute(stmt):
        counts[object_id]["channels"] += n
        if level:
            counts[object_id][level] += n
    return counts


def _build_nodes(idx: ObjectIndex, direct: dict[int, dict]) -> dict[int, schemas.ObjectNode]:
    """Сворачивает счётчики снизу вверх: цвет узла — максимальный риск внутри."""
    nodes: dict[int, schemas.ObjectNode] = {}

    def build(object_id: int) -> schemas.ObjectNode:
        info = idx.nodes[object_id]
        children = [build(c) for c in info.children]
        own = direct.get(object_id, {"channels": 0, **{lvl: 0 for lvl in LEVELS}})
        risk_counts = {lvl: own[lvl] + sum(ch.risk_counts[lvl] for ch in children) for lvl in LEVELS}
        present = [lvl for lvl in LEVELS if risk_counts[lvl] > 0]
        node = schemas.ObjectNode(
            id=info.id,
            name=info.name,
            level=info.level,
            kind=info.kind,
            parent_id=info.parent_id,
            max_risk_level=max(present, key=RISK_ORDER.__getitem__) if present else None,
            risk_counts=risk_counts,
            channels_count=own["channels"] + sum(ch.channels_count for ch in children),
            children=children,
        )
        nodes[object_id] = node
        return node

    for root in idx.roots():
        build(root.id)
    return nodes


@router.get("", response_model=schemas.ObjectTree, summary="Дерево «район → объект → подобъект» с риском")
def object_tree(
    system_type: str | None = Query(None, description="Учитывать только датчики этой системы"),
    sensor_type: str | None = Query(None, description="Учитывать только датчики этого типа"),
    at=Depends(at_param),
    access: Access = Depends(get_access),
    db: Session = Depends(get_db),
):
    idx = ObjectIndex.load(db, access)
    snap = snapshot_at(db, at)
    nodes = _build_nodes(idx, _direct_counts(db, idx, snap, system_type, sensor_type))
    return schemas.ObjectTree(at=at, snapshot_at=snap, items=[nodes[r.id] for r in idx.roots()])


@router.get(
    "/{object_id}",
    response_model=schemas.ObjectDetail,
    summary="Объект, его дочерние объекты и датчики",
    responses={404: {"model": schemas.ErrorResponse}},
)
def object_detail(
    object_id: int,
    system_type: str | None = None,
    sensor_type: str | None = None,
    risk_level: list[schemas.RiskLevel] | None = Query(None),
    q: str | None = Query(None, description="Поиск по названию, тегу или id датчика"),
    page: Page = Depends(page_param),
    at=Depends(at_param),
    access: Access = Depends(get_access),
    db: Session = Depends(get_db),
):
    idx = ObjectIndex.load(db, access)
    if object_id not in idx.nodes:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Объект не найден")
    snap = snapshot_at(db, at)
    nodes = _build_nodes(idx, _direct_counts(db, idx, snap, system_type, sensor_type))
    node = nodes[object_id]
    return schemas.ObjectDetail(
        at=at,
        object=node.model_copy(update={"children": []}),
        path=idx.path(object_id),
        children=[c.model_copy(update={"children": []}) for c in node.children],
        channels=channel_page(
            db, idx, at, snap, page, object_id=object_id, system_type=system_type,
            sensor_type=sensor_type, risk_level=risk_level, q=q,
        ),
    )
