"""Отказы каналов по разметке ML (3 вида) — для отметок на графике карточки датчика.

Уникальный ключ (channel_id, ts, kind) одновременно служит индексом для выборки «отказы канала за период».

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-25 10:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('channel_faults',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('channel_id', sa.BigInteger(), nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.CheckConstraint("kind IN ('Неисправен', 'Отключено устройство', 'Пропадание связи')", name='ck_channel_faults_kind'),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('channel_id', 'ts', 'kind', name='uq_channel_faults')
    )


def downgrade() -> None:
    op.drop_table('channel_faults')
