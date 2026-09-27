import { DownloadOutlined } from '@ant-design/icons';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Alert, App, Button, Card, DatePicker, Input, Select, Space, Switch, Table, Tooltip, TreeSelect, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs, { type Dayjs } from 'dayjs';
import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '@/api/endpoints';
import { ChannelLink, DecisionTag, OutcomeTag, ProbText, WorkOrderTag } from '@/components/cells';
import { DecisionModal, type DecisionTarget } from '@/components/DecisionModal';
import { MainFactor } from '@/components/Factors';
import { QueryState } from '@/components/QueryState';
import { RiskBadge } from '@/components/RiskBadge';
import { SensorFilters } from '@/components/SensorFilters';
import { WorkOrderModal, type WorkOrderSource } from '@/components/WorkOrderModal';
import { useAt } from '@/hooks/useAt';
import { useAuth } from '@/hooks/useAuth';
import type { JournalItem, JournalQuery, RiskLevel } from '@/types';
import { objectTreeData } from '@/utils/objects';
import { DECISION_LABELS } from '@/utils/labels';
import { RISK_LEVELS, RISK_META } from '@/utils/risk';
import { saveResponse } from '@/utils/download';
import { fmtDateTime } from '@/utils/time';
import { EventsJournal, JournalTabs } from '@/components/EventsJournal';

interface Filters {
  range?: [Dayjs, Dayjs] | null;
  risk_level?: RiskLevel[];
  decision?: JournalQuery['decision'];
  outcome?: JournalQuery['outcome'];
  object_id?: number;
  system?: string;
  sensor?: string;
  q?: string;
  only_alerts: boolean;
}

function PredictionsJournal() {
  const [at] = useAt();
  const { canAct } = useAuth();
  const { message } = App.useApp();
  // Переход из уведомления «ещё N датчиков перешли в «Критично»»: ?slice=<срез>&risk=critical
  const [params, setParams] = useSearchParams();
  const slice = params.get('slice');
  const [filters, setFilters] = useState<Filters>(() => {
    const risk = params.get('risk') as RiskLevel | null;
    return { only_alerts: true, risk_level: risk && RISK_LEVELS.includes(risk) ? [risk] : undefined };
  });
  const clearSlice = () =>
    setParams((prev) => {
      const p = new URLSearchParams(prev);
      p.delete('slice');
      p.delete('risk');
      return p;
    });
  const [pg, setPg] = useState({ current: 1, pageSize: 50 });
  const [search, setSearch] = useState('');
  const [decision, setDecision] = useState<DecisionTarget | null>(null);
  const [woSource, setWoSource] = useState<WorkOrderSource | null>(null);
  const [exporting, setExporting] = useState(false);

  // Если «сейчас» позже последнего среза, по умолчанию показываем 7 дней до среза, а не пустой журнал
  const summary = useQuery({ queryKey: ['summary', at], queryFn: () => api.summary(at), enabled: !at });
  const objects = useQuery({ queryKey: ['objects', at, null, null], queryFn: () => api.objects({ at }), staleTime: 5 * 60_000 });
  const snapshot = summary.data?.snapshot_at;
  const staleNow = !at && !!snapshot && dayjs().diff(dayjs(snapshot), 'hour') > 24;

  const query: Omit<JournalQuery, 'limit' | 'offset'> = useMemo(() => {
    const range = filters.range ?? (staleNow ? [dayjs(snapshot).subtract(7, 'day'), dayjs(snapshot)] : null);
    return {
      at,
      date_from: slice ?? range?.[0].format('YYYY-MM-DD'),
      date_to: slice ?? range?.[1].format('YYYY-MM-DD'),
      risk_level: filters.risk_level?.length ? filters.risk_level : undefined,
      decision: filters.decision,
      outcome: filters.outcome,
      object_id: filters.object_id,
      system_type: filters.system,
      sensor_type: filters.sensor,
      q: filters.q || undefined,
      only_alerts: filters.only_alerts,
    };
  }, [at, filters, staleNow, snapshot, slice]);

  const waiting = !at && summary.isPending;
  const q = useQuery({
    queryKey: ['journal', query, pg],
    queryFn: () => api.journal({ ...query, limit: pg.pageSize, offset: (pg.current - 1) * pg.pageSize }),
    placeholderData: keepPreviousData,
    enabled: !waiting,
  });

  const set = (patch: Partial<Filters>) => {
    if (patch.range !== undefined && slice) clearSlice();
    setFilters((f) => ({ ...f, ...patch }));
    setPg((p) => ({ ...p, current: 1 }));
  };

  const exportXlsx = async () => {
    setExporting(true);
    try {
      const res = await api.exportJournal(query);
      await saveResponse(res, `journal_${dayjs().format('YYYY-MM-DD')}.xlsx`);
    } catch (e) {
      message.error(e instanceof Error ? e.message : 'Не удалось выгрузить журнал');
    } finally {
      setExporting(false);
    }
  };

  const columns: ColumnsType<JournalItem> = [
    { title: 'Время прогноза', dataIndex: 'at', width: 140, render: (v: string) => fmtDateTime(v) },
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
      title: 'Вероятность',
      dataIndex: 'prob',
      width: 190,
      render: (p: number, r) => (
        <Space size={6}>
          <ProbText prob={p} level={r.risk_level} />
          <RiskBadge level={r.risk_level} />
        </Space>
      ),
    },
    {
      title: 'Главная причина',
      key: 'factor',
      width: 250,
      responsive: ['xl'],
      render: (_, r) => (
        <>
          <MainFactor p={r} />
          {r.likely_kind_label && (
            <div>
              <Typography.Text type="secondary">{r.likely_kind_label}</Typography.Text>
            </div>
          )}
        </>
      ),
    },
    {
      title: 'Решение диспетчера',
      dataIndex: 'decision',
      width: 190,
      render: (d, r) =>
        d || !canAct ? (
          <DecisionTag decision={d} />
        ) : (
          <Button
            size="small"
            onClick={() =>
              setDecision({ prediction_id: r.prediction_id, channelName: r.channel.name, objectName: r.object.name, prob: r.prob, risk_level: r.risk_level })
            }
          >
            Принять решение
          </Button>
        ),
    },
    {
      title: 'Рекомендация',
      key: 'recommendation',
      width: 220,
      responsive: ['xxl'],
      render: (_, r) =>
        r.recommendation ? (
          <Tooltip title={`${r.recommendation.fault_kind_label}. ${r.recommendation.action}`} overlayStyle={{ maxWidth: 440 }}>
            <span className="main-factor">{r.recommendation.title}</span>
          </Tooltip>
        ) : (
          '—'
        ),
    },
    { title: 'Исход', dataIndex: 'outcome', width: 120, render: (o) => <OutcomeTag outcome={o} /> },
    {
      title: 'Заявка',
      width: 150,
      render: (_, r) =>
        r.work_order_id ? (
          <WorkOrderTag id={r.work_order_id} status={r.work_order_status} number={r.work_order_number ?? undefined} />
        ) : canAct ? (
          <Button size="small" type="link" onClick={() => setWoSource({ ...r })} style={{ padding: 0 }}>
            Создать заявку
          </Button>
        ) : (
          '—'
        ),
    },
  ];

  return (
    <>
      <div className="page-title">
        <Space size={16} wrap>
          <Typography.Title level={3}>Журнал</Typography.Title>
          <JournalTabs />
        </Space>
        <Button icon={<DownloadOutlined />} onClick={exportXlsx} loading={exporting}>
          Выгрузка в XLSX
        </Button>
      </div>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space wrap size={[8, 8]}>
          <Tooltip title="Не выбран — 7 дней до выбранного момента">
            <DatePicker.RangePicker
              value={filters.range ?? (staleNow ? [dayjs(snapshot).subtract(7, 'day'), dayjs(snapshot)] : null)}
              onChange={(v) => set({ range: v as [Dayjs, Dayjs] | null })}
              format="DD.MM.YYYY"
              placeholder={['Период: с', 'по']}
              allowEmpty={[false, false]}
            />
          </Tooltip>
          <Select
            mode="multiple"
            allowClear
            placeholder="Уровень риска"
            style={{ minWidth: 190 }}
            value={filters.risk_level}
            onChange={(v) => set({ risk_level: v })}
            options={RISK_LEVELS.map((l) => ({ value: l, label: RISK_META[l].label }))}
            maxTagCount="responsive"
          />
          <Select
            allowClear
            placeholder="Решение"
            style={{ width: 190 }}
            value={filters.decision}
            onChange={(v) => set({ decision: v })}
            options={[{ value: 'none', label: 'Без решения' }, ...Object.entries(DECISION_LABELS).map(([value, label]) => ({ value, label }))]}
          />
          <Select
            allowClear
            placeholder="Исход"
            style={{ width: 150 }}
            value={filters.outcome}
            onChange={(v) => set({ outcome: v })}
            options={[
              { value: 'happened', label: 'Сбылся' },
              { value: 'not_happened', label: 'Не сбылся' },
              { value: 'unknown', label: 'Ожидается' },
            ]}
          />
          <TreeSelect
            allowClear
            showSearch
            treeNodeFilterProp="title"
            placeholder="Объект"
            style={{ width: 220 }}
            value={filters.object_id}
            onChange={(v) => set({ object_id: v })}
            treeData={objectTreeData(objects.data?.items ?? [])}
            treeDefaultExpandAll={false}
          />
          <SensorFilters system={filters.system} sensor={filters.sensor} onChange={(v) => set(v)} />
          <Input.Search
            allowClear
            placeholder="Датчик: название, тег, номер"
            style={{ width: 260 }}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              if (!e.target.value) set({ q: undefined });
            }}
            onSearch={(v) => set({ q: v })}
          />
          <Space size={6}>
            <Switch checked={filters.only_alerts} onChange={(v) => set({ only_alerts: v })} aria-label="Только тревожные" />
            <span>Только тревожные и с решением</span>
          </Space>
        </Space>
      </Card>
      {slice && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`Показан один срез прогнозов — ${fmtDateTime(slice)} (переход из уведомления).`}
          action={
            <Button size="small" onClick={clearSlice}>
              Показать весь период
            </Button>
          }
        />
      )}
      {staleNow && !filters.range && !slice && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={`Последний срез прогнозов — ${fmtDateTime(snapshot)}. Журнал показан за 7 дней до него.`}
        />
      )}
      <Card size="small">
        <QueryState query={{ ...q, isPending: q.isPending || waiting }} rows={12} isEmpty={(d) => d.total === 0}>
          {(d) => (
            <Table
              size="small"
              rowKey="prediction_id"
              columns={columns}
              dataSource={d.items}
              loading={q.isFetching}
              rowClassName={(r) => `row-${r.risk_level}`}
              locale={{ emptyText: 'Рисков не обнаружено' }}
              scroll={{ x: 1100 }}
              pagination={{
                current: pg.current,
                pageSize: pg.pageSize,
                total: d.total,
                showSizeChanger: true,
                pageSizeOptions: [20, 50, 100, 200],
                showTotal: (t) => `Всего записей: ${t.toLocaleString('ru-RU')}`,
                onChange: (current, pageSize) => setPg({ current: pageSize !== pg.pageSize ? 1 : current, pageSize }),
              }}
            />
          )}
        </QueryState>
      </Card>
      <DecisionModal target={decision} onClose={() => setDecision(null)} />
      <WorkOrderModal open={!!woSource} source={woSource} onClose={() => setWoSource(null)} />
    </>
  );
}

/** Журнал: вкладки «Прогнозы» (прогнозы с решениями и исходами) и «События» (тревожные события с контекстом) */
export default function JournalPage() {
  const [params] = useSearchParams();
  return params.get('tab') === 'events' ? <EventsJournal /> : <PredictionsJournal />;
}
