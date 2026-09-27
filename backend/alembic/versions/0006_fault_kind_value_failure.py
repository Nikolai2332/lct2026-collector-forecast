"""Вид отказа «Сбой значения» (метка v3: значение «01.01.1970» или аномальный газ вместо показания).

Только расширяет проверку допустимых видов в channel_faults: существующие строки не меняются.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-26 08:40:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('channel_faults') as batch:
        batch.drop_constraint('ck_channel_faults_kind', type_='check')
        batch.create_check_constraint(
            'ck_channel_faults_kind',
            "kind IN ('Неисправен', 'Отключено устройство', 'Пропадание связи', 'Сбой значения')")


def downgrade() -> None:
    op.execute("DELETE FROM channel_faults WHERE kind = 'Сбой значения'")
    with op.batch_alter_table('channel_faults') as batch:
        batch.drop_constraint('ck_channel_faults_kind', type_='check')
        batch.create_check_constraint(
            'ck_channel_faults_kind', "kind IN ('Неисправен', 'Отключено устройство', 'Пропадание связи')")
