"""Настраиваемые параметры, которые администратор меняет без перезапуска (границы уровней риска, лимиты уведомлений).

Только добавляет таблицу: прогнозы, исходы и прочие данные не меняются. Пустая таблица = значения по умолчанию.
Почему таблица, а не файл: настройки должны пережить пересборку контейнера и попасть в резервные копии БД.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-25 23:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('app_settings',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('updated_by', sa.String(length=64), nullable=True),
    sa.PrimaryKeyConstraint('key')
    )


def downgrade() -> None:
    op.drop_table('app_settings')
