"""Источник решения диспетчера: user — принято в интерфейсе, simulation — смоделировано (docs/SIMULATED_DECISIONS.md).

Только добавляет колонку со значением по умолчанию user: существующие решения не меняются.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-26 19:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('decisions') as batch:
        batch.add_column(sa.Column('source', sa.String(length=16), server_default='user', nullable=False))
        batch.create_check_constraint('ck_decisions_source', "source IN ('user', 'simulation')")


def downgrade() -> None:
    with op.batch_alter_table('decisions') as batch:
        batch.drop_constraint('ck_decisions_source', type_='check')
        batch.drop_column('source')
