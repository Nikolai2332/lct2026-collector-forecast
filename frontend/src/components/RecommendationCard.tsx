import { FileAddOutlined, StopOutlined, ToolOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Col, Row, Skeleton, Space, Tag, Tooltip, Typography } from 'antd';
import { useState } from 'react';
import { useRecommendation } from '@/api/queries';
import { useAuth } from '@/hooks/useAuth';
import type { ChannelCard } from '@/types';
import { RISK_META } from '@/utils/risk';
import { WO_PRIORITY_COLORS } from '@/utils/labels';
import { dueText, fmtDateTime } from '@/utils/time';
import { OpenWorkOrderNotice } from './cells';
import { WorkOrderModal } from './WorkOrderModal';

/**
 * Рекомендация по ТО рядом с прогнозом: что сделать, почему (факты), приоритет, срок, кому и чего не делать.
 * Правила прозрачные (не ML) — backend/app/recommendations/rules.yaml, проект правил.
 */
export function RecommendationCard({ card }: { card: ChannelCard }) {
  const p = card.prediction;
  const { canAct } = useAuth();
  const q = useRecommendation(p?.prediction_id);
  const [open, setOpen] = useState(false);
  if (!p) return null;
  const a = q.data;
  return (
    <Card
      size="small"
      className="recommendation-card"
      title={
        <Space>
          <ToolOutlined />
          Рекомендация по обслуживанию
          {a && (
            <Tooltip title={`На чём основан вид отказа: ${a.fault_kind_basis}`}>
              <Tag>{a.fault_kind_label}</Tag>
            </Tooltip>
          )}
        </Space>
      }
      extra={
        <Tooltip title={a?.source}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            проект правил
          </Typography.Text>
        </Tooltip>
      }
    >
      {q.isLoading && <Skeleton active paragraph={{ rows: 2 }} />}
      {q.isError && <Alert type="warning" showIcon message="Рекомендацию получить не удалось" description={(q.error as Error).message} />}
      {a && (
        <Row gutter={[24, 12]} align="top">
          <Col xs={24} xl={18}>
            <Typography.Paragraph strong style={{ fontSize: 16, marginBottom: 6 }} data-testid="advice-action">
              {a.action}
            </Typography.Paragraph>
            <Typography.Paragraph style={{ marginBottom: 6 }} data-testid="advice-reason">
              <Typography.Text type="secondary">Почему: </Typography.Text>
              {a.reason}
            </Typography.Paragraph>
            {(a.avoid ?? []).map((x) => (
              <div key={x} style={{ color: RISK_META.risk.color }}>
                <StopOutlined /> {x}
              </div>
            ))}
          </Col>
          <Col xs={24} xl={6}>
            <Space direction="vertical" size={6} style={{ width: '100%' }}>
              <span>
                Приоритет: <Tag color={WO_PRIORITY_COLORS[a.priority]}>{a.priority_label}</Tag>
              </span>
              {a.assignee !== '—' && (
                <>
                  <span>
                    Срок: <b>{dueText(a.due_hours)}</b>{' '}
                    <Typography.Text type="secondary">(до {fmtDateTime(a.due_at)})</Typography.Text>
                  </span>
                  {a.due_basis && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {a.due_basis[0].toUpperCase() + a.due_basis.slice(1)}
                    </Typography.Text>
                  )}
                  <span>
                    Кому: <b>{a.assignee}</b>
                  </span>
                </>
              )}
              {canAct && a.work_order_needed && card.open_work_orders.length === 0 && (
                <Button type="primary" ghost block icon={<FileAddOutlined />} onClick={() => setOpen(true)}>
                  Заявка по рекомендации
                </Button>
              )}
              {canAct && a.work_order_needed && card.open_work_orders.length > 0 && (
                <OpenWorkOrderNotice orders={card.open_work_orders} />
              )}
            </Space>
          </Col>
        </Row>
      )}
      <WorkOrderModal open={open} source={open ? { ...p } : null} onClose={() => setOpen(false)} />
    </Card>
  );
}
