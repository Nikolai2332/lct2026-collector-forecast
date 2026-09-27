import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Card, Col, Input, Row, Segmented, Select, Space, Table, Tooltip, TreeSelect, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '@/api/endpoints';
import { ChannelLink, ProbText } from '@/components/cells';
import { CollectorScheme } from '@/components/CollectorScheme';
import { ObjectTiles } from '@/components/ObjectTiles';
import { QueryState } from '@/components/QueryState';
import { RiskBadge } from '@/components/RiskBadge';
import { SensorFilters } from '@/components/SensorFilters';
import { useAt } from '@/hooks/useAt';
import type { ChannelListItem, ObjectNode, RiskLevel } from '@/types';
import { byUrgency, critOf } from '@/utils/objects';
import { RISK_LEVELS, RISK_META } from '@/utils/risk';
import { fmtDateTime, fmtNum } from '@/utils/time';

function RiskCounts({ node }: { node: ObjectNode }) {
  return (
    <Space size={4} wrap>
      {RISK_LEVELS.filter((l) => (node.risk_counts[l] ?? 0) > 0 && l !== 'normal').map((l) => (
        <RiskBadge key={l} level={l} suffix={node.risk_counts[l]} />
      ))}
      <Typography.Text type="secondary">{fmtNum(node.channels_count)} датч.</Typography.Text>
    </Space>
  );
}

interface ChannelsPanelProps {
  objectId: number;
  system?: string;
  sensor?: string;
}

/** Датчики выбранного узла (с вложенными), по убыванию риска; серверная пагинация */
function ChannelsPanel({ objectId, system, sensor }: ChannelsPanelProps) {
  const [at] = useAt();
  const [levels, setLevels] = useState<RiskLevel[]>([]);
  const [q, setQ] = useState<string>();
  const [pg, setPg] = useState({ current: 1, pageSize: 20 });
  const query = useQuery({
    queryKey: ['objectDetail', objectId, at, system, sensor, levels, q, pg],
    queryFn: () =>
      api.object(objectId, {
        at,
        system_type: system,
        sensor_type: sensor,
        risk_level: levels.length ? levels : undefined,
        q,
        limit: pg.pageSize,
        offset: (pg.current - 1) * pg.pageSize,
      }),
    placeholderData: keepPreviousData,
  });
  const columns: ColumnsType<ChannelListItem> = [
    { title: 'Риск', width: 120, render: (_, r) => <RiskBadge level={r.prediction?.risk_level} /> },
    {
      title: 'Датчик',
      render: (_, r) => (
        <Space direction="vertical" size={0}>
          <ChannelLink id={r.id} name={r.name} />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {r.sensor_type} · {r.tag}
          </Typography.Text>
        </Space>
      ),
    },
    { title: 'Подобъект', render: (_, r) => <Tooltip title={r.object.path.join(' → ')}>{r.object.name}</Tooltip> },
    {
      title: 'Вероятность',
      width: 100,
      align: 'right',
      render: (_, r) => (r.prediction ? <ProbText prob={r.prediction.prob} level={r.prediction.risk_level} /> : '—'),
    },
    { title: 'Здоровье', width: 90, align: 'right', render: (_, r) => r.prediction?.health ?? '—' },
  ];
  return (
    <QueryState query={query} rows={10}>
      {(d) => (
        <Card
          size="small"
          title={
            <Space direction="vertical" size={0}>
              <span>
                Датчики: {d.path.join(' → ')}
              </span>
              <RiskCounts node={d.object} />
            </Space>
          }
        >
          <Space wrap style={{ marginBottom: 8 }}>
            <Select
              mode="multiple"
              allowClear
              placeholder="Уровень риска"
              style={{ minWidth: 200 }}
              value={levels}
              onChange={(v) => {
                setLevels(v);
                setPg((p) => ({ ...p, current: 1 }));
              }}
              options={RISK_LEVELS.map((l) => ({ value: l, label: RISK_META[l].label }))}
              maxTagCount="responsive"
            />
            <Input.Search
              allowClear
              placeholder="Название, тег, номер"
              style={{ width: 240 }}
              onSearch={(v) => {
                setQ(v || undefined);
                setPg((p) => ({ ...p, current: 1 }));
              }}
            />
          </Space>
          <Table
            size="small"
            rowKey="id"
            columns={columns}
            dataSource={d.channels.items}
            loading={query.isFetching}
            locale={{ emptyText: 'Рисков не обнаружено' }}
            rowClassName={(r) => (r.prediction ? `row-${r.prediction.risk_level}` : '')}
            pagination={{
              ...pg,
              total: d.channels.total,
              showSizeChanger: true,
              pageSizeOptions: [20, 50, 100],
              size: 'small',
              showTotal: (t) => `Датчиков: ${t.toLocaleString('ru-RU')}`,
              onChange: (current, pageSize) => setPg({ current: pageSize !== pg.pageSize ? 1 : current, pageSize }),
            }}
            scroll={{ x: 560 }}
          />
        </Card>
      )}
    </QueryState>
  );
}

export default function ObjectsPage() {
  const [at] = useAt();
  const [params, setParams] = useSearchParams();
  const [system, setSystem] = useState<string>();
  const [sensor, setSensor] = useState<string>();
  const [view, setView] = useState<'all' | 'risk'>('all');
  const selected = params.get('object') ? Number(params.get('object')) : null;
  // «Плитки» — дерево объектов; «Схема коллектора» — линейная схема по пикетам (синтетическая геометрия)
  const mode = params.get('view') === 'scheme' ? 'scheme' : 'tiles';
  const setMode = (m: string) =>
    setParams((prev) => {
      const p = new URLSearchParams(prev);
      if (m === 'scheme') p.set('view', 'scheme');
      else p.delete('view');
      return p;
    });
  const q = useQuery({
    queryKey: ['objects', at, system ?? null, sensor ?? null],
    queryFn: () => api.objects({ at, system_type: system, sensor_type: sensor }),
    placeholderData: keepPreviousData,
  });
  const select = (id: number) =>
    setParams((prev) => {
      const p = new URLSearchParams(prev);
      p.set('object', String(id));
      return p;
    });

  return (
    <>
      <div className="page-title">
        <Typography.Title level={3}>Схема объектов</Typography.Title>
        <Space wrap>
          <Segmented
            value={mode}
            onChange={(v) => setMode(String(v))}
            options={[
              { value: 'tiles', label: 'Плитки' },
              { value: 'scheme', label: 'Схема коллектора' },
            ]}
            aria-label="Вид схемы объектов"
          />
          {mode === 'scheme' && (
            <TreeSelect
              allowClear
              placeholder="Район или объект"
              style={{ width: 240 }}
              value={selected ?? undefined}
              treeDefaultExpandAll
              treeData={(q.data?.items ?? []).map((d) => ({
                value: d.id,
                title: d.name,
                children: (d.children ?? []).map((o) => ({ value: o.id, title: o.name })),
              }))}
              onChange={(v?: number) =>
                setParams((prev) => {
                  const p = new URLSearchParams(prev);
                  if (v) p.set('object', String(v));
                  else p.delete('object');
                  return p;
                })
              }
              aria-label="Район или объект"
            />
          )}
          <SensorFilters
            system={system}
            sensor={sensor}
            onChange={(v) => {
              setSystem(v.system);
              setSensor(v.sensor);
            }}
          />
          {mode === 'tiles' && (
            <Select
              value={view}
              onChange={setView}
              style={{ width: 230 }}
              options={[
                { value: 'all', label: 'Все подобъекты' },
                { value: 'risk', label: 'Только с риском и критичными' },
              ]}
            />
          )}
        </Space>
      </div>
      {mode === 'scheme' ? (
        <CollectorScheme system={system} sensor={sensor} objectId={selected} />
      ) : (
      <QueryState query={q} isEmpty={(d) => d.items.length === 0} emptyText="Объектов нет" skeletonHeight={480}>
        {(d) => (
          <Row gutter={[16, 16]}>
            <Col xs={24} xl={14}>
              {d.items.map((district) => {
                const subMax = Math.max(1, ...(district.children ?? []).flatMap((o) => (o.children ?? []).map(critOf)));
                return (
                <Card
                  key={district.id}
                  size="small"
                  title={
                    <Space wrap>
                      <a onClick={() => select(district.id)}>{district.name}</a>
                      <RiskCounts node={district} />
                    </Space>
                  }
                  extra={<Typography.Text type="secondary">Срез: {fmtDateTime(d.snapshot_at)}</Typography.Text>}
                >
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(330px, 1fr))', gap: 12 }}>
                    {/* Одна шкала яркости для всех подобъектов района: 1 критичный везде выглядит одинаково */}
                    {[...(district.children ?? [])].sort(byUrgency).map((obj) => {
                      const subs = (obj.children ?? []).filter(
                        (s) => view === 'all' || s.max_risk_level === 'risk' || s.max_risk_level === 'critical',
                      );
                      const level = obj.max_risk_level;
                      const c = critOf(obj);
                      return (
                        <Card
                          key={obj.id}
                          size="small"
                          type="inner"
                          style={{
                            borderLeft: `${c ? 6 : 3}px solid ${c ? RISK_META.critical.color : level ? RISK_META[level].color : 'var(--tile-empty-border)'}`,
                            boxShadow: selected === obj.id ? '0 0 0 2px #1677ff' : undefined,
                          }}
                          title={
                            <Space>
                              <a onClick={() => select(obj.id)}>{obj.name}</a>
                              <RiskBadge level={level} iconOnly />
                            </Space>
                          }
                          extra={
                            c ? (
                              <span style={{ color: RISK_META.critical.color }}>
                                <b style={{ fontSize: 16 }}>{c}</b> крит. из {fmtNum(obj.channels_count)}
                              </span>
                            ) : (
                              <Typography.Text type="secondary">критичных нет · {fmtNum(obj.channels_count)} датч.</Typography.Text>
                            )
                          }
                        >
                          {subs.length ? (
                            <ObjectTiles nodes={subs} minWidth={140} selectedId={selected} onSelect={(n) => select(n.id)} compact scaleMax={subMax} />
                          ) : (
                            <Typography.Text type="secondary">Рисков не обнаружено</Typography.Text>
                          )}
                        </Card>
                      );
                    })}
                  </div>
                </Card>
                );
              })}
            </Col>
            <Col xs={24} xl={10}>
              <div style={{ position: 'sticky', top: 72 }}>
                {selected ? (
                  <ChannelsPanel key={selected} objectId={selected} system={system} sensor={sensor} />
                ) : (
                  <Card size="small">
                    <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
                      Выберите объект или подобъект на схеме — здесь появится список его датчиков по убыванию риска. Цвет плитки — максимальный риск
                      внутри; уровень продублирован значком и подписью.
                    </Typography.Paragraph>
                  </Card>
                )}
              </div>
            </Col>
          </Row>
        )}
      </QueryState>
      )}
    </>
  );
}
