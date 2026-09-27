import { PlusOutlined } from '@ant-design/icons';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Button, Card, Input, Segmented, Select, Space, Table, Tag, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import { useState } from 'react';
import { api } from '@/api/endpoints';
import { AtLink, ChannelLink } from '@/components/cells';
import { MaintenancePlanPanel } from '@/components/MaintenancePlanPanel';
import { QueryState } from '@/components/QueryState';
import { StatusActions } from '@/components/StatusActions';
import { WorkOrderModal } from '@/components/WorkOrderModal';
import { useAt } from '@/hooks/useAt';
import { useAuth } from '@/hooks/useAuth';
import type { WorkOrderOut, WorkOrderPriority, WorkOrderStatus } from '@/types';
import {
  WO_OPEN_STATUSES,
  WO_PRIORITY_COLORS,
  WO_PRIORITY_LABELS,
  WO_STATUS_COLORS,
  WO_STATUS_LABELS,
} from '@/utils/labels';
import { fmtDateTime } from '@/utils/time';

type Scope = 'open' | 'drafts' | 'all' | 'plan';
const SCOPE_STATUSES: Record<Scope, WorkOrderStatus[] | undefined> = {
  open: WO_OPEN_STATUSES,
  drafts: ['draft'],
  all: undefined,
  plan: undefined,
};

export default function WorkOrdersPage() {
  const [at] = useAt();
  const { canAct, canChangeStatus } = useAuth();
  const [scope, setScope] = useState<Scope>('open');
  const [statuses, setStatuses] = useState<WorkOrderStatus[]>([]);
  const [priorities, setPriorities] = useState<WorkOrderPriority[]>([]);
  const [q, setQ] = useState<string>();
  const [pg, setPg] = useState({ current: 1, pageSize: 20 });
  const [creating, setCreating] = useState(false);
  const status = statuses.length ? statuses : SCOPE_STATUSES[scope];
  const query = useQuery({
    queryKey: ['workOrders', at, status, priorities, q, pg],
    queryFn: () =>
      api.workOrders({
        at,
        status,
        priority: priorities.length ? priorities : undefined,
        q,
        limit: pg.pageSize,
        offset: (pg.current - 1) * pg.pageSize,
      }),
    placeholderData: keepPreviousData,
    enabled: scope !== 'plan',
  });
  const now = at ? dayjs(at) : dayjs();
  const reset = () => setPg((p) => ({ ...p, current: 1 }));

  const columns: ColumnsType<WorkOrderOut> = [
    { title: 'Номер', dataIndex: 'number', width: 150, render: (n: string, r) => <AtLink to={`/work-orders/${r.id}`}>{n}</AtLink> },
    { title: 'Статус', dataIndex: 'status', width: 120, render: (s: WorkOrderStatus, r) => <Tag color={WO_STATUS_COLORS[s]}>{r.status_label}</Tag> },
    { title: 'Приоритет', dataIndex: 'priority', width: 110, render: (p: WorkOrderPriority, r) => <Tag color={WO_PRIORITY_COLORS[p]}>{r.priority_label}</Tag> },
    {
      title: 'Датчик',
      render: (_, r) => (
        <Space direction="vertical" size={0}>
          <ChannelLink id={r.channel.id} name={r.channel.name} />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {r.channel.sensor_type}
          </Typography.Text>
        </Space>
      ),
    },
    { title: 'Объект', render: (_, r) => <Tooltip title={r.object.path.join(' → ')}>{r.object.name}</Tooltip> },
    {
      title: 'Срок',
      dataIndex: 'due_at',
      width: 150,
      render: (d: string | null, r) => {
        const overdue = d && WO_OPEN_STATUSES.includes(r.status) && dayjs(d).isBefore(now);
        return overdue ? (
          <Tooltip title="Срок истёк">
            <Typography.Text type="danger" strong>
              {fmtDateTime(d)} ⚠
            </Typography.Text>
          </Tooltip>
        ) : (
          fmtDateTime(d)
        );
      },
    },
    { title: 'Исполнитель', dataIndex: 'assignee', responsive: ['xxl'], render: (a) => a ?? '—' },
    { title: 'Создана', dataIndex: 'created_at', width: 140, responsive: ['xl'], render: (d: string) => fmtDateTime(d) },
    ...(canChangeStatus ? [{ title: 'Сменить статус', width: 250, render: (_: unknown, r: WorkOrderOut) => <StatusActions wo={r} /> }] : []),
  ];

  return (
    <>
      <div className="page-title">
        <Typography.Title level={3}>Заявки</Typography.Title>
        {canAct && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>
            Создать заявку
          </Button>
        )}
      </div>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space wrap>
          <Segmented
            value={scope}
            onChange={(v) => {
              setScope(v as Scope);
              setStatuses([]);
              reset();
            }}
            options={[
              { value: 'open', label: 'Открытые' },
              { value: 'drafts', label: 'Черновики' },
              { value: 'all', label: 'Все' },
              { value: 'plan', label: 'Рекомендованные работы' },
            ]}
          />
          {scope !== 'plan' && (
            <>
              <Select
                mode="multiple"
                allowClear
                placeholder="Статус"
                style={{ minWidth: 200 }}
                value={statuses}
                onChange={(v) => {
                  setStatuses(v);
                  reset();
                }}
                options={Object.entries(WO_STATUS_LABELS).map(([value, label]) => ({ value, label }))}
                maxTagCount="responsive"
              />
              <Select
                mode="multiple"
                allowClear
                placeholder="Приоритет"
                style={{ minWidth: 200 }}
                value={priorities}
                onChange={(v) => {
                  setPriorities(v);
                  reset();
                }}
                options={Object.entries(WO_PRIORITY_LABELS).map(([value, label]) => ({ value, label }))}
                maxTagCount="responsive"
              />
              <Input.Search
                allowClear
                placeholder="Номер, датчик, объект, исполнитель"
                style={{ width: 300 }}
                onSearch={(v) => {
                  setQ(v || undefined);
                  reset();
                }}
              />
            </>
          )}
        </Space>
      </Card>
      {scope === 'plan' ? (
        <MaintenancePlanPanel />
      ) : (
        <Card size="small">
          <QueryState query={query} rows={10} isEmpty={(d) => d.total === 0} emptyText="Заявок нет">
            {(d) => (
              <Table
                size="small"
                rowKey="id"
                columns={columns}
                dataSource={d.items}
                loading={query.isFetching}
                scroll={{ x: 1000 }}
                pagination={{
                  ...pg,
                  total: d.total,
                  showSizeChanger: true,
                  pageSizeOptions: [20, 50, 100],
                  showTotal: (t) => `Заявок: ${t}`,
                  onChange: (current, pageSize) => setPg({ current: pageSize !== pg.pageSize ? 1 : current, pageSize }),
                }}
              />
            )}
          </QueryState>
        </Card>
      )}
      <WorkOrderModal open={creating} onClose={() => setCreating(false)} />
    </>
  );
}
