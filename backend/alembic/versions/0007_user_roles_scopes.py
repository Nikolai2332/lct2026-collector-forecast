"""Ролевая модель заказчика: роли складываются (user_roles), область видимости — узлы дерева объектов (user_scopes).

Только добавляет таблицы и расширяет список допустимых ролей в users.role; прогнозы, исходы, события, отказы,
решения и заявки не меняются. Существующие пользователи получают роль из users.role; тем, у кого роль с областью
(диспетчер района, руководитель), — область «все районы» (все объекты уровня 1): видят то же, что и раньше.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-26 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ROLES = ("dispatcher", "engineer", "manager", "admin")
ROLES = ("dispatcher_ods", "dispatcher", "technician", "engineer", "manager", "admin")
SCOPED_ROLES = ("dispatcher", "technician", "manager")


def _in(values: tuple[str, ...]) -> str:
    return f"role IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table('user_roles',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.CheckConstraint(_in(ROLES), name='ck_user_roles_role'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'role')
    )
    op.create_table('user_scopes',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('object_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['object_id'], ['objects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'object_id')
    )
    with op.batch_alter_table('users') as batch:
        batch.drop_constraint('ck_users_role', type_='check')
        batch.create_check_constraint('ck_users_role', _in(ROLES))
    op.execute("INSERT INTO user_roles (user_id, role) SELECT id, role FROM users")
    op.execute(
        "INSERT INTO user_scopes (user_id, object_id) SELECT u.id, o.id FROM users u CROSS JOIN objects o "
        f"WHERE o.level = 1 AND u.role IN ({', '.join(repr(r) for r in SCOPED_ROLES)})"
    )


def downgrade() -> None:
    op.drop_table('user_scopes')
    op.drop_table('user_roles')
    op.execute(f"UPDATE users SET role = 'dispatcher' WHERE role NOT IN ({', '.join(repr(r) for r in OLD_ROLES)})")
    with op.batch_alter_table('users') as batch:
        batch.drop_constraint('ck_users_role', type_='check')
        batch.create_check_constraint('ck_users_role', _in(OLD_ROLES))
