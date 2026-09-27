"""Состояние симуляции потока событий (одна строка).

Симуляция только читает прогнозы и исходы; здесь — её «модельные часы» и счётчики.
При старте API строка сбрасывается в «неактивна» (app.sim.reset_on_startup).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-25 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('sim_state',
    sa.Column('id', sa.SmallInteger(), autoincrement=False, nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('day', sa.Date(), nullable=True),
    sa.Column('speed', sa.SmallInteger(), nullable=False),
    sa.Column('model_start', sa.DateTime(), nullable=True),
    sa.Column('model_time', sa.DateTime(), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('started_by', sa.String(length=64), nullable=True),
    sa.Column('stopped_at', sa.DateTime(), nullable=True),
    sa.Column('stop_reason', sa.String(length=32), nullable=True),
    sa.Column('last_snapshot', sa.DateTime(), nullable=True),
    sa.Column('events_received', sa.Integer(), nullable=False),
    sa.Column('events_accepted', sa.Integer(), nullable=False),
    sa.Column('critical_sent', sa.Integer(), nullable=False),
    sa.CheckConstraint('id = 1', name='ck_sim_state_single_row'),
    sa.CheckConstraint('speed BETWEEN 1 AND 600', name='ck_sim_state_speed'),
    sa.PrimaryKeyConstraint('id')
    )
    op.execute("INSERT INTO sim_state (id, active, speed, events_received, events_accepted, critical_sent) "
               "VALUES (1, false, 60, 0, 0, 0)")


def downgrade() -> None:
    op.drop_table('sim_state')
