import { FileAddOutlined } from '@ant-design/icons';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Space, Table, Tag, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMemo, useState } from 'react';
import { api } from '@/api/endpoints';
import { invalidateAfterWorkOrder } from '@/api/queries';
import { useAt } from '@/hooks/useAt';
import { useAuth } from '@/hooks/useAuth';
import type { MaintenancePlanItem, WorkOrderPriority } from '@/types';
import { WO_PRIORITY_COLORS, WO_PRIORITY_LABELS } from '@/utils/labels';
import { dueText, fmtDateTime, fmtPct } from '@/utils/time';
import { ChannelLink } from './cells';
import { QueryState } from './QueryState';
import { RiskBadge } from './RiskBadge';

interface Row {
  key: string;
  kind: 'object' | 'item';
  name: string;
  path?: string[];
  count?: number;
  priority: WorkOrderPriority;
  item?: MaintenancePlanItem;
  children?: Row[];
}

const PAGE = 20;

/** «Рекомендованные работы»: профилактика по правилам ТО, сгруппированная по объектам, и черновики заявок выбранным. */
export function MaintenancePlanPanel() {
  const [at] = useAt();
  const { canAct } = useAuth();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<React.Key[]>([]);
  const query = useQuery({
    queryKey: ['maintenancePlan', at, page],
    queryFn: () => api.maintenancePlan({ at, limit: PAGE, offset: (page - 1) * PAGE }),
    placeholderData: keepPreviousData,
  });
  const rows: Row[] = useMemo(
    () =>
      (query.data?.groups ?? []).map((g) => ({
        key: `o-${g.object.id}`,
        kind: 'object',
        name: g.object.name,
        path: g.object.path,
        count: g.items.length,
        priority: g.top_priority,
        children: g.items.map((it) => ({ key: `p-${it.prediction_id}`, kind: 'item', name: it.channel.name, priority: it.advice.priority, item: it })),
      })),
    [query.data],
  );
  const ids = selected.map(String).filter((k) => k.startsWith('p-')).map((k) => Number(k.slice(2)));

  const create = useMutation({
    mutationFn: () => api.maintenanceDrafts(ids, at),
    onSuccess: (r) => {
      invalidateAfterWorkOrder(qc);
      setSelected([]);
      const numbers = r.created.map((w) => w.number).join(', ');
      message.success({
        content: `Черновиков создано: ${r.created.length}${numbers ? ` (${numbers})` : ''}. Пропущено: ${r.skipped.length}${
          r.skipped.length ? ` — ${[...new Set(r.skipped.map((s) => s.reason))].join('; ')}` : ''
        }`,
        duration: 8,
      });
    },
    onError: (e) => message.error((e as Error).message),
  });

  const columns: ColumnsType<Row> = [
    {
      title: 'Объект / датчик',
      width: 300,
      render: (_, r) =>
        r.kind === 'object' ? (
          <Tooltip title={r.path?.join(' → ')}>
            <b>{r.name}</b> <Typography.Text type="secondary">· работ: {r.count}</Typography.Text>
          </Tooltip>
        ) : (
          <Space direction="vertical" size={0}>
            <ChannelLink id={r.item!.channel.id} name={r.name} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {r.item!.channel.sensor_type} · {fmtPct(r.item!.prob)} <RiskBadge level={r.item!.risk_level} />
            </Typography.Text>
          </Space>
        ),
    },
    {
      title: 'Работа',
      width: 340,
      render: (_, r) =>
        r.item ? (
          <Tooltip title={r.item.advice.action} overlayStyle={{ maxWidth: 440 }}>
            <b>{r.item.advice.title}</b>
            <div style={{ fontSize: 12 }}>{r.item.advice.action}</div>
          </Tooltip>
        ) : null,
    },
    {
      title: 'Почему',
      responsive: ['xl'],
      render: (_, r) =>
        r.item ? (
          <Tooltip title={r.item.advice.reason} overlayStyle={{ maxWidth: 520 }}>
            <span className="clamp-2">{r.item.advice.reason}</span>
          </Tooltip>
        ) : null,
    },
    {
      title: 'Приоритет',
      width: 110,
      render: (_, r) => <Tag color={WO_PRIORITY_COLORS[r.priority]}>{WO_PRIORITY_LABELS[r.priority]}</Tag>,
    },
    {
      title: 'Срок',
      width: 150,
      render: (_, r) =>
        r.item ? (
          <Tooltip title={`до ${fmtDateTime(r.item.advice.due_at)}`}>{dueText(r.item.advice.due_hours)}</Tooltip>
        ) : null,
    },
    { title: 'Кому', width: 190, responsive: ['xxl'], render: (_, r) => r.item?.advice.assignee ?? null },
  ];

  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="Профилактика по правилам ТО на ближайшие дни"
        description={
          <>
            Датчики с повторными отказами, повтором после ремонта, недостоверными показаниями, проблемами агрегатов, питания
            или связи объекта — и без открытой заявки. Правила прозрачные, не ML. {query.data?.source ?? 'Проект правил'}.
            {query.data?.snapshot_at && <> Срез прогнозов: {fmtDateTime(query.data.snapshot_at)}.</>}
          </>
        }
      />
      <Card
        size="small"
        title={query.data ? `Работ: ${query.data.total_items} в ${query.data.total_objects} объектах` : 'Рекомендованные работы'}
        extra={
          canAct && (
            <Button type="primary" icon={<FileAddOutlined />} disabled={!ids.length} loading={create.isPending} onClick={() => create.mutate()}>
              Создать черновики заявок{ids.length ? ` (${ids.length})` : ''}
            </Button>
          )
        }
      >
        <QueryState query={query} rows={8} isEmpty={(d) => d.total_items === 0} emptyText="Рекомендованных работ нет">
          {(d) => (
            <Table<Row>
              size="small"
              rowKey="key"
              columns={columns}
              dataSource={rows}
              loading={query.isFetching}
              scroll={{ x: 1000 }}
              expandable={{ defaultExpandedRowKeys: rows.slice(0, 1).filter((r) => (r.count ?? 0) <= 10).map((r) => r.key) }}
              rowSelection={
                canAct
                  ? { selectedRowKeys: selected, onChange: setSelected, checkStrictly: false, preserveSelectedRowKeys: true }
                  : undefined
              }
              pagination={{
                current: page,
                pageSize: PAGE,
                total: d.total_objects,
                showSizeChanger: false,
                showTotal: (t) => `Объектов: ${t}`,
                onChange: setPage,
              }}
            />
          )}
        </QueryState>
      </Card>
    </>
  );
}
