import { CheckCircleOutlined, ClockCircleOutlined, MinusCircleOutlined } from '@ant-design/icons';
import { Space, Tag, Tooltip, Typography } from 'antd';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useAtHref } from '@/hooks/useAt';
import type { DecisionOut, Outcome, RiskLevel, WorkOrderStatus } from '@/types';
import { DECISION_COLORS, WO_STATUS_COLORS, WO_STATUS_LABELS } from '@/utils/labels';
import { RISK_META } from '@/utils/risk';
import { fmtDateTime, fmtPct } from '@/utils/time';

/** Ссылка на экран с сохранением «машины времени» */
export function AtLink({ to, children }: { to: string; children: ReactNode }) {
  const href = useAtHref();
  return <Link to={href(to)}>{children}</Link>;
}

export function ChannelLink({ id, name }: { id: number; name: string }) {
  return <AtLink to={`/channels/${id}`}>{name}</AtLink>;
}

export function ProbText({ prob, level }: { prob: number; level: RiskLevel }) {
  return (
    <Typography.Text strong style={{ color: RISK_META[level].color }}>
      {fmtPct(prob)}
    </Typography.Text>
  );
}

export function OutcomeTag({ outcome }: { outcome?: Outcome | null }) {
  if (!outcome)
    return (
      <Tooltip title="Исход прогноза ещё неизвестен: 24 ч после него не прошли">
        <Tag icon={<ClockCircleOutlined />} color="default">
          Ожидается
        </Tag>
      </Tooltip>
    );
  return outcome.happened ? (
    <Tooltip title={`Отказ случился в течение 24 ч после прогноза: ${fmtDateTime(outcome.fault_at)}`}>
      <Tag icon={<CheckCircleOutlined />} color="red">
        Сбылся
      </Tag>
    </Tooltip>
  ) : (
    <Tooltip title="За 24 ч после прогноза отказа не было">
      <Tag icon={<MinusCircleOutlined />} color="green">
        Не сбылся
      </Tag>
    </Tooltip>
  );
}

/** Решение смоделировано (журнала ОДС у заказчика нет) — docs/SIMULATED_DECISIONS.md */
export function SimulatedBadge() {
  return (
    <Tooltip title="Решение смоделировано: журнала решений диспетчеров у заказчика нет, история построена по правилам (docs/SIMULATED_DECISIONS.md)">
      <Tag bordered={false} color="default" style={{ fontSize: 11, marginInlineEnd: 0 }}>
        смоделировано
      </Tag>
    </Tooltip>
  );
}

export function DecisionTag({ decision }: { decision?: DecisionOut | null }) {
  if (!decision) return <Typography.Text type="secondary">Нет решения</Typography.Text>;
  return (
    <Tooltip
      title={
        <>
          <div>Причина: {decision.reason.name}</div>
          {decision.comment && <div>Комментарий: {decision.comment}</div>}
          <div>
            {decision.user?.full_name ?? '—'}, {fmtDateTime(decision.created_at)}
          </div>
        </>
      }
    >
      <Space size={2}>
        <Tag color={DECISION_COLORS[decision.decision_type]} style={{ marginInlineEnd: 0 }}>
          {decision.decision_label}
        </Tag>
        {decision.source === 'simulation' && <SimulatedBadge />}
      </Space>
    </Tooltip>
  );
}

export function WorkOrderTag({ id, status, number }: { id: number; status?: WorkOrderStatus | null; number?: string }) {
  return (
    <AtLink to={`/work-orders/${id}`}>
      <Tag color={status ? WO_STATUS_COLORS[status] : 'blue'} style={{ cursor: 'pointer' }}>
        {number ?? `№ ${id}`}
        {status && ` · ${WO_STATUS_LABELS[status]}`}
      </Tag>
    </AtLink>
  );
}
