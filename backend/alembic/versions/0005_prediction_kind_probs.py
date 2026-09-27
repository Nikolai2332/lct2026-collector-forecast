"""Вероятности видов отказа модели v3 (kind_probs) у прогнозов.

Только добавляет nullable-колонку: существующие прогнозы, решения и заявки не меняются. У прогнозов v2 и у
прогнозов уровня «норма» v3 поле пустое (NULL) — вид отказа тогда выводится движком рекомендаций из причин.
Формат: {"link": 0.61, "fault": 0.02, "disconnected": 0.0, "value": 0.0}, сумма = prob.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26 05:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('predictions', sa.Column(
        'kind_probs', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True))


def downgrade() -> None:
    op.drop_column('predictions', 'kind_probs')
