"""Начальная схема: справочники, прогнозы, решения, заявки, метрики, пользователи, аудит.

Индекс (at, prob) обслуживает срез «прогнозы на момент at по убыванию риска»:
PostgreSQL читает его в обратном порядке, отдельный DESC не нужен.
Миграции пишутся только под PostgreSQL 16; тесты на SQLite строят схему через metadata.create_all.

Revision ID: 0001
Revises: 
Create Date: 2026-09-24 15:41:32.869331
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('model_metrics',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('model_version', sa.String(length=64), nullable=False),
    sa.Column('scope', sa.String(length=32), nullable=False),
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('precision', sa.Float(), nullable=True),
    sa.Column('recall', sa.Float(), nullable=True),
    sa.Column('f1', sa.Float(), nullable=True),
    sa.Column('pr_auc', sa.Float(), nullable=True),
    sa.Column('support', sa.Integer(), nullable=True),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('period_from', sa.Date(), nullable=True),
    sa.Column('period_to', sa.Date(), nullable=True),
    sa.Column('computed_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('model_version', 'scope', 'key', name='uq_model_metrics')
    )
    op.create_table('model_thresholds',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('model_version', sa.String(length=64), nullable=False),
    sa.Column('threshold', sa.Float(), nullable=False),
    sa.Column('precision', sa.Float(), nullable=False),
    sa.Column('recall', sa.Float(), nullable=False),
    sa.Column('alerts_per_day', sa.Float(), nullable=False),
    sa.Column('tp_per_day', sa.Float(), nullable=False),
    sa.Column('fp_per_day', sa.Float(), nullable=False),
    sa.Column('is_selected', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('model_version', 'threshold', name='uq_model_thresholds')
    )
    op.create_table('objects',
    sa.Column('id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('level', sa.SmallInteger(), nullable=False),
    sa.Column('parent_id', sa.Integer(), nullable=True),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.CheckConstraint('level BETWEEN 1 AND 3', name='ck_objects_level'),
    sa.ForeignKeyConstraint(['parent_id'], ['objects.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_objects_parent_id'), 'objects', ['parent_id'], unique=False)
    op.create_table('reasons',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('decision_type', sa.String(length=16), nullable=True),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('recommendations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('sensor_type', sa.String(length=128), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_index(op.f('ix_recommendations_sensor_type'), 'recommendations', ['sensor_type'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('full_name', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=True),
    sa.Column('auth_source', sa.String(length=16), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('last_login_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint("role IN ('dispatcher', 'engineer', 'manager', 'admin')", name='ck_users_role'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('username', sa.String(length=64), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('entity', sa.String(length=64), nullable=True),
    sa.Column('entity_id', sa.String(length=64), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_log_action'), 'audit_log', ['action'], unique=False)
    op.create_index(op.f('ix_audit_log_ts'), 'audit_log', ['ts'], unique=False)
    op.create_index(op.f('ix_audit_log_user_id'), 'audit_log', ['user_id'], unique=False)
    op.create_table('channels',
    sa.Column('id', sa.BigInteger(), autoincrement=False, nullable=False),
    sa.Column('object_id', sa.Integer(), nullable=False),
    sa.Column('system_type', sa.String(length=128), nullable=False),
    sa.Column('sensor_type', sa.String(length=128), nullable=False),
    sa.Column('tag', sa.String(length=64), nullable=False),
    sa.Column('tag_l1', sa.String(length=16), nullable=True),
    sa.Column('tag_l2', sa.String(length=16), nullable=True),
    sa.Column('tag_l3', sa.String(length=16), nullable=True),
    sa.Column('tag_l4', sa.String(length=16), nullable=True),
    sa.Column('tag_l5', sa.String(length=16), nullable=True),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.ForeignKeyConstraint(['object_id'], ['objects.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_channels_object_id'), 'channels', ['object_id'], unique=False)
    op.create_index('ix_channels_sensor_type', 'channels', ['sensor_type'], unique=False)
    op.create_index('ix_channels_system_sensor', 'channels', ['system_type', 'sensor_type'], unique=False)
    op.create_table('channel_daily',
    sa.Column('channel_id', sa.BigInteger(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('events_count', sa.Integer(), nullable=False),
    sa.Column('alarm_count', sa.Integer(), nullable=False),
    sa.Column('fault_count', sa.Integer(), nullable=False),
    sa.Column('uncertain_count', sa.Integer(), nullable=False),
    sa.Column('power_off_count', sa.Integer(), nullable=False),
    sa.Column('status_changes', sa.Integer(), nullable=False),
    sa.Column('value_avg', sa.Float(), nullable=True),
    sa.Column('value_min', sa.Float(), nullable=True),
    sa.Column('value_max', sa.Float(), nullable=True),
    sa.Column('max_gap_min', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('channel_id', 'day')
    )
    op.create_index('ix_channel_daily_day', 'channel_daily', ['day'], unique=False)
    op.create_table('events_recent',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('source_event_id', sa.BigInteger(), nullable=True),
    sa.Column('channel_id', sa.BigInteger(), nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.Column('is_alarm', sa.Boolean(), nullable=False),
    sa.Column('value_raw', sa.String(length=128), nullable=False),
    sa.Column('value_num', sa.Float(), nullable=True),
    sa.Column('value_text', sa.String(length=128), nullable=True),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('channel_id', 'ts', 'value_raw', name='uq_events_recent_dedup')
    )
    op.create_index('ix_events_recent_ts', 'events_recent', ['ts'], unique=False)
    op.create_table('predictions',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('channel_id', sa.BigInteger(), nullable=False),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('horizon_h', sa.SmallInteger(), nullable=False),
    sa.Column('prob', sa.Float(), nullable=False),
    sa.Column('health', sa.SmallInteger(), nullable=False),
    sa.Column('risk_level', sa.String(length=16), nullable=False),
    sa.Column('top_factors', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('model_version', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.CheckConstraint("risk_level IN ('normal', 'attention', 'risk', 'critical')", name='ck_predictions_risk_level'),
    sa.CheckConstraint('health >= 0 AND health <= 100', name='ck_predictions_health'),
    sa.CheckConstraint('horizon_h > 0', name='ck_predictions_horizon'),
    sa.CheckConstraint('prob >= 0 AND prob <= 1', name='ck_predictions_prob'),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('channel_id', 'at', 'horizon_h', name='uq_predictions_channel_at')
    )
    op.create_index('ix_predictions_at_prob', 'predictions', ['at', 'prob'], unique=False)
    op.create_index('ix_predictions_at_risk', 'predictions', ['at', 'risk_level'], unique=False)
    op.create_index('ix_predictions_channel_at', 'predictions', ['channel_id', 'at'], unique=False)
    op.create_table('decisions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('prediction_id', sa.BigInteger(), nullable=False),
    sa.Column('decision_type', sa.String(length=16), nullable=False),
    sa.Column('reason_id', sa.Integer(), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("decision_type IN ('dispatch', 'false_alarm', 'monitor')", name='ck_decisions_type'),
    sa.ForeignKeyConstraint(['prediction_id'], ['predictions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['reason_id'], ['reasons.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_decisions_prediction_created', 'decisions', ['prediction_id', 'created_at'], unique=False)
    op.create_table('prediction_outcomes',
    sa.Column('prediction_id', sa.BigInteger(), nullable=False),
    sa.Column('happened', sa.Boolean(), nullable=False),
    sa.Column('fault_at', sa.DateTime(), nullable=True),
    sa.Column('fault_kind', sa.String(length=64), nullable=True),
    sa.Column('labeled_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['prediction_id'], ['predictions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('prediction_id')
    )
    op.create_table('work_orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('number', sa.String(length=32), nullable=False),
    sa.Column('prediction_id', sa.BigInteger(), nullable=True),
    sa.Column('channel_id', sa.BigInteger(), nullable=False),
    sa.Column('object_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('priority', sa.String(length=16), nullable=False),
    sa.Column('reason_id', sa.Integer(), nullable=True),
    sa.Column('recommendation_id', sa.Integer(), nullable=True),
    sa.Column('recommendation_text', sa.Text(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('assignee', sa.String(length=255), nullable=True),
    sa.Column('due_at', sa.DateTime(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint("priority IN ('low', 'medium', 'high', 'critical')", name='ck_work_orders_priority'),
    sa.CheckConstraint("status IN ('draft', 'submitted', 'in_progress', 'done', 'cancelled')", name='ck_work_orders_status'),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['object_id'], ['objects.id'], ),
    sa.ForeignKeyConstraint(['prediction_id'], ['predictions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['reason_id'], ['reasons.id'], ),
    sa.ForeignKeyConstraint(['recommendation_id'], ['recommendations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('number')
    )
    op.create_index(op.f('ix_work_orders_channel_id'), 'work_orders', ['channel_id'], unique=False)
    op.create_index(op.f('ix_work_orders_object_id'), 'work_orders', ['object_id'], unique=False)
    op.create_index(op.f('ix_work_orders_prediction_id'), 'work_orders', ['prediction_id'], unique=False)
    op.create_index('ix_work_orders_status_created', 'work_orders', ['status', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_work_orders_status_created', table_name='work_orders')
    op.drop_index(op.f('ix_work_orders_prediction_id'), table_name='work_orders')
    op.drop_index(op.f('ix_work_orders_object_id'), table_name='work_orders')
    op.drop_index(op.f('ix_work_orders_channel_id'), table_name='work_orders')
    op.drop_table('work_orders')
    op.drop_table('prediction_outcomes')
    op.drop_index('ix_decisions_prediction_created', table_name='decisions')
    op.drop_table('decisions')
    op.drop_index('ix_predictions_channel_at', table_name='predictions')
    op.drop_index('ix_predictions_at_risk', table_name='predictions')
    op.drop_index('ix_predictions_at_prob', table_name='predictions')
    op.drop_table('predictions')
    op.drop_index('ix_events_recent_ts', table_name='events_recent')
    op.drop_table('events_recent')
    op.drop_index('ix_channel_daily_day', table_name='channel_daily')
    op.drop_table('channel_daily')
    op.drop_index('ix_channels_system_sensor', table_name='channels')
    op.drop_index('ix_channels_sensor_type', table_name='channels')
    op.drop_index(op.f('ix_channels_object_id'), table_name='channels')
    op.drop_table('channels')
    op.drop_index(op.f('ix_audit_log_user_id'), table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_ts'), table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_action'), table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_table('users')
    op.drop_index(op.f('ix_recommendations_sensor_type'), table_name='recommendations')
    op.drop_table('recommendations')
    op.drop_table('reasons')
    op.drop_index(op.f('ix_objects_parent_id'), table_name='objects')
    op.drop_table('objects')
    op.drop_table('model_thresholds')
    op.drop_table('model_metrics')
