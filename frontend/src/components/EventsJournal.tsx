import { DownloadOutlined } from '@ant-design/icons';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { App, Button, Card, DatePicker, Input, Select, Space, Switch, Table, Tabs, Tag, Tooltip, TreeSelect, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs, { type Dayjs } from 'dayjs';
import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '@/api/endpoints';
import { ChannelLink } from '@/components/cells';
import { QueryState } from '@/components/QueryState';
import { SensorFilters } from '@/components/SensorFilters';
import { useAt } from '@/hooks/useAt';
import type { EventItem, EventsQuery } from '@/types';
import { saveResponse } from '@/utils/download';
import { objectTreeData } from '@/utils/objects';
import { fmtDateTime } from '@/utils/time';

type TagCode = EventItem['tags'][number]['code'];

/** Контекст события: цвет и короткая подпись; полное «почему» — в подсказке */
const EVENT_TAG_META: Record<TagCode, { color: string; short: string }> = {
  planned_check: { color: 'blue', short: 'Плановая проверка?' },
  group_off: { color: 'purple', short: 'Групповое отключение' },
  value_failure: { color: 'volcano', short: 'Сбой значения' },
};

export function EventTags({ item }: { item: EventItem }) {
  if (!item.tags.length) return <Typography.Text type="secondary">—</Typography.Text>;
  return (
    <Space size={[4, 4]} wrap>
      {item.tags.map((t) => (
        <Tooltip key={t.code} title={`${t.label}: ${t.reason}`}>
          <Tag color={EVENT_TAG_META[t.code].color} style={{ marginInlineEnd: 0 }}>
            {EVENT_TAG_META[t.code].short}
          </Tag>
        </Tooltip>
      ))}
    </Space>
  );
}

/** Переключатель вкладок журнала (?tab=events) — общий для «Прогнозов» и «Событий» */
export function JournalTabs() {
  const [params, setParams] = useSearchParams();
  return (
    <Tabs
      activeKey={params.get('tab') === 'events' ? 'events' : 'predictions'}
      onChange={(k) =>
        setParams((prev) => {
          const p = new URLSearchParams(prev);
          if (k === 'events') p.set('tab', 'events');
          else p.delete('tab');
          return p;
        })
      }
      items={[
        { key: 'predictions', label: 'Прогнозы' },
        { key: 'events', label: 'События' },
      ]}
      style={{ marginBottom: -16 }}
    />
  );
}

interface Filters {
  range?: [Dayjs, Dayjs] | null;
  object_id?: number;
  system?: string;
  sensor?: string;
  q?: string;
  only_alarms: boolean;
  kind: NonNullable<EventsQuery['kind']>;
  tag?: TagCode;
}

export function EventsJournal() {
  const [at] = useAt();
  const { message } = App.useApp();
  const [filters, setFilters] = useState<Filters>({ only_alarms: true, kind: 'status' });
  const [pg, setPg] = useState({ current: 1, pageSize: 50 });
  const [search, setSearch] = useState('');
  const [exporting, setExporting] = useState(false);
  const objects = useQuery({ queryKey: ['objects', at, null, null], queryFn: () => api.objects({ at }), staleTime: 5 * 60_000 });
  const query = useMemo<Omit<EventsQuery, 'limit' | 'offset'>>(
    () => ({
      at,
      date_from: filters.range?.[0]?.startOf('day').format('YYYY-MM-DDTHH:mm:ss'),
      date_to: filters.range?.[1]?.endOf('day').format('YYYY-MM-DDTHH:mm:ss'),
      object_id: filters.object_id,
      system_type: filters.system,
      sensor_type: filters.sensor,
      q: filters.q,
      only_alarms: filters.only_alarms,
      kind: filters.kind,
      tag: filters.tag,
    }),
    [at, filters],
  );
  const q = useQuery({
    queryKey: ['events', query, pg],
    queryFn: () => api.events({ ...query, limit: pg.pageSize, offset: (pg.current - 1) * pg.pageSize }),
    placeholderData: keepPreviousData,
  });
  const set = (patch: Partial<Filters>) => {
    setFilters((f) => ({ ...f, ...patch }));
    setPg((p) => ({ ...p, current: 1 }));
  };
  const exportXlsx = async () => {
    setExporting(true);
    try {
      await saveResponse(await api.exportEvents(query), `events_${dayjs().format('YYYY-MM-DD')}.xlsx`);
    } catch (e) {
      message.error(e instanceof Error ? e.message : 'Не удалось выгрузить журнал событий');
    } finally {
      setExporting(false);
    }
  };

  const columns: ColumnsType<EventItem> = [
    { title: 'Время', dataIndex: 'ts', width: 150, render: (v: string) => dayjs(v).format('DD.MM.YYYY HH:mm:ss') },
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
    { title: 'Пикет', width: 130, render: (_, r) => r.picket ?? '—' },
    {
      title: 'Значение',
      width: 190,
      render: (_, r) => (
        <Space direction="vertical" size={0}>
          <span>{r.value}</span>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {r.value_kind_label}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: 'Тревожное',
      width: 100,
      render: (_, r) => (r.is_alarm ? <Tag color="red">тревожное</Tag> : <Typography.Text type="secondary">нет</Typography.Text>),
    },
    { title: 'Контекст (правила)', width: 230, render: (_, r) => <EventTags item={r} /> },
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
          <Tooltip title="Не выбран — сутки до выбранного момента (или до последнего события); не длиннее 31 дня">
            <DatePicker.RangePicker
              value={filters.range ?? null}
              onChange={(v) => set({ range: v as [Dayjs, Dayjs] | null })}
              format="DD.MM.YYYY"
              placeholder={['Период: с', 'по']}
            />
          </Tooltip>
          <TreeSelect
            allowClear
            placeholder="Объект"
            style={{ width: 220 }}
            value={filters.object_id}
            treeData={objectTreeData(objects.data?.items ?? [])}
            onChange={(v?: number) => set({ object_id: v })}
            treeDefaultExpandAll={false}
            showSearch
            treeNodeFilterProp="title"
          />
          <SensorFilters system={filters.system} sensor={filters.sensor} onChange={(v) => set(v)} />
          <Select
            style={{ width: 200 }}
            value={filters.kind}
            onChange={(v) => set({ kind: v })}
            options={[
              { value: 'status', label: 'Смены статуса' },
              { value: 'values', label: 'Показания' },
              { value: 'all', label: 'Все события' },
            ]}
            aria-label="Тип событий"
          />
          <Select
            allowClear
            placeholder="Контекст"
            style={{ width: 220 }}
            value={filters.tag}
            onChange={(v) => set({ tag: v })}
            options={(Object.keys(EVENT_TAG_META) as TagCode[]).map((c) => ({ value: c, label: EVENT_TAG_META[c].short }))}
            aria-label="Контекст"
          />
          <Input.Search
            allowClear
            placeholder="Датчик: название, тег, номер"
            style={{ width: 240 }}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              if (!e.target.value) set({ q: undefined });
            }}
            onSearch={(v) => set({ q: v || undefined })}
          />
          <Space size={6}>
            <Switch checked={filters.only_alarms} onChange={(v) => set({ only_alarms: v })} aria-label="Только тревожные" />
            <span>Только тревожные</span>
          </Space>
        </Space>
      </Card>
      <Card size="small">
        <QueryState query={q} rows={12} isEmpty={(d) => d.total === 0} emptyText="Событий за период нет">
          {(d) => (
            <>
              <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
                Период: {fmtDateTime(d.date_from)} — {fmtDateTime(d.date_to)}. Контекст — прозрачные правила, не модель:
                газ в будни 9–14 — вероятная плановая проверка; одновременное отключение ≥ 80 % однотипных датчиков
                объекта — вероятно, плановые работы; служебный код или дата вместо показания — сбой значения.
                {filters.tag && ' Фильтр по контексту применяется к странице результата.'}
              </Typography.Paragraph>
              <Table
                size="small"
                rowKey="id"
                columns={columns}
                dataSource={d.items}
                loading={q.isFetching}
                scroll={{ x: 1100 }}
                pagination={{
                  current: pg.current,
                  pageSize: pg.pageSize,
                  total: d.total,
                  showSizeChanger: true,
                  pageSizeOptions: [20, 50, 100, 200],
                  showTotal: (t) => `Событий: ${t.toLocaleString('ru-RU')}`,
                  onChange: (current, pageSize) => setPg({ current: pageSize !== pg.pageSize ? 1 : current, pageSize }),
                }}
              />
            </>
          )}
        </QueryState>
      </Card>
    </>
  );
}
