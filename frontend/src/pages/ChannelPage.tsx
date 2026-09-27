import dayjs from 'dayjs';
import { EyeOutlined, FileAddOutlined, FormOutlined, WarningOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import {
  Breadcrumb,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Progress,
  Row,
  Segmented,
  Space,
  Table,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from 'antd';
import { useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '@/api/endpoints';
import { AtLink, DecisionTag, OutcomeTag, SimulatedBadge, WorkOrderTag } from '@/components/cells';
import { Chart } from '@/components/Chart';
import { DecisionModal, type DecisionTarget } from '@/components/DecisionModal';
import { FactorList } from '@/components/Factors';
import { QueryState } from '@/components/QueryState';
import { RecommendationCard } from '@/components/RecommendationCard';
import { RiskBadge } from '@/components/RiskBadge';
import { WorkOrderModal, type WorkOrderSource } from '@/components/WorkOrderModal';
import { useAt } from '@/hooks/useAt';
import { useAuth } from '@/hooks/useAuth';
import type { ChannelCard, ChannelHistory, DecisionType, EventItem, PredictionPoint } from '@/types';
import { EventTags } from '@/components/EventsJournal';
import { RISK_META } from '@/utils/risk';
import { fmtDateTime, fmtNum, fmtPct } from '@/utils/time';

function PredictionBlock({ card }: { card: ChannelCard }) {
  const { canAct, user } = useAuth();
  const [decision, setDecision] = useState<{ target: DecisionTarget; type?: DecisionType } | null>(null);
  const [wo, setWo] = useState<WorkOrderSource | null>(null);
  const p = card.prediction;
  if (!p)
    return (
      <Card>
        <Empty description="Прогноза на этот момент нет — выберите более поздний момент или «Сейчас»" />
      </Card>
    );
  const m = RISK_META[p.risk_level];
  const target: DecisionTarget = { prediction_id: p.prediction_id, channelName: card.name, objectName: card.object.name, prob: p.prob, risk_level: p.risk_level };
  const openWO = card.open_work_orders;
  return (
    <Card
      className="prediction-card"
      title={
        <Space>
          <WarningOutlined style={{ color: m.color }} />
          {`Прогноз отказа на ${p.horizon_h ?? 24} ч`}
          <Typography.Text type="secondary" style={{ fontWeight: 400 }}>
            срез {fmtDateTime(p.at)} · модель {p.model_version}
          </Typography.Text>
        </Space>
      }
      style={{ borderTop: `3px solid ${m.color}` }}
    >
      <Row gutter={[24, 16]} align="top">
        <Col xs={24} md={8} xl={5} style={{ textAlign: 'center' }}>
          <Progress
            type="dashboard"
            percent={p.health}
            strokeColor={m.color}
            format={(v) => (
              <span>
                <b style={{ fontSize: 28 }}>{v}</b>
                <br />
                <span style={{ fontSize: 12, color: 'var(--app-muted)' }}>индекс здоровья</span>
              </span>
            )}
            size={150}
            aria-label={`Индекс здоровья ${p.health} из 100`}
          />
          <div>
            <RiskBadge level={p.risk_level} />
          </div>
        </Col>
        <Col xs={24} md={16} xl={5}>
          <Typography.Text type="secondary">Вероятность отказа в ближайшие 24 ч</Typography.Text>
          <div style={{ fontSize: 40, fontWeight: 700, color: m.color, lineHeight: 1.2 }}>{fmtPct(p.prob)}</div>
          <Space direction="vertical" size={4} style={{ marginTop: 8 }}>
            <span>
              Исход: <OutcomeTag outcome={p.outcome} />
            </span>
            <span>
              Решение: <DecisionTag decision={p.decision} />
            </span>
            {card.on_watch && (
              <Tag icon={<EyeOutlined />} color="blue">
                На контроле
              </Tag>
            )}
          </Space>
        </Col>
        <Col xs={24} xl={8}>
          <Typography.Title level={5} style={{ marginTop: 0 }}>
            Почему модель так считает
          </Typography.Title>
          {p.likely_kind_label && (
            <Typography.Paragraph strong style={{ marginBottom: 8 }}>
              {p.likely_kind_label}
            </Typography.Paragraph>
          )}
          {p.factors_human?.length || p.factors.length ? (
            <FactorList p={p} />
          ) : (
            <Typography.Text type="secondary">
              Риск низкий — причины модель рассчитывает для уровней «Внимание» и выше.
            </Typography.Text>
          )}
        </Col>
        <Col xs={24} xl={6}>
          <Space direction="vertical" style={{ width: '100%' }}>
            {canAct ? (
              <>
                <Button type="primary" size="large" block icon={<FileAddOutlined />} onClick={() => setWo({ ...p })}>
                  Создать заявку
                </Button>
                <Button block icon={<FormOutlined />} onClick={() => setDecision({ target })}>
                  Отметить решение
                </Button>
                <Button block icon={<EyeOutlined />} onClick={() => setDecision({ target, type: 'monitor' })} disabled={card.on_watch}>
                  {card.on_watch ? 'Уже на контроле' : 'Взять на контроль'}
                </Button>
              </>
            ) : (
              <Typography.Text type="secondary">Роль «{user?.role_label}» — только просмотр</Typography.Text>
            )}
            {openWO.length > 0 && (
              <div>
                <Typography.Text type="secondary">Открытые заявки:</Typography.Text>
                <div>
                  {openWO.map((w) => (
                    <WorkOrderTag key={w.id} id={w.id} number={w.number} status={w.status} />
                  ))}
                </div>
              </div>
            )}
          </Space>
        </Col>
      </Row>
      {p.decisions.length > 0 && (
        <>
          <Typography.Title level={5}>История решений по прогнозу</Typography.Title>
          <Timeline
            items={p.decisions
              .slice()
              .reverse()
              .map((d) => ({
                color: d.decision_type === 'dispatch' ? 'red' : d.decision_type === 'monitor' ? 'blue' : 'gray',
                children: (
                  <>
                    <b>{d.decision_label}</b> — {d.reason.name} {d.source === 'simulation' && <SimulatedBadge />}
                    {d.comment && <> · «{d.comment}»</>}
                    <br />
                    <Typography.Text type="secondary">
                      {d.user?.full_name ?? '—'}, {fmtDateTime(d.created_at)}
                    </Typography.Text>
                  </>
                ),
              }))}
          />
        </>
      )}
      <DecisionModal target={decision?.target ?? null} initialType={decision?.type} onClose={() => setDecision(null)} />
      <WorkOrderModal open={!!wo} source={wo} onClose={() => setWo(null)} />
    </Card>
  );
}

/** Виды отказов из разметки ML (как в `/api/channels/{id}/history`) — своя отметка на графике у каждого */
const FAULT_KIND_STYLE: Record<string, { label: string; color: string; type: 'solid' | 'dashed' | 'dotted' }> = {
  'Неисправен': { label: '⚠ Неисправен', color: RISK_META.critical.color, type: 'solid' },
  'Отключено устройство': { label: '⏻ Отключено', color: '#722ed1', type: 'dashed' },
  'Пропадание связи': { label: '⌁ Нет связи', color: '#595959', type: 'dotted' },
  'Сбой значения': { label: '⌀ Сбой значения', color: '#d48806', type: 'dashed' },
};
const faultStyle = (kind: string) => FAULT_KIND_STYLE[kind] ?? { label: `⚠ ${kind}`, color: RISK_META.critical.color, type: 'solid' as const };

function historyOption(h: ChannelHistory, numeric: boolean) {
  const x = (t: string) => t.replace('T', ' ');
  const faults = h.faults.map((f) => {
    const st = faultStyle(f.kind);
    return {
      xAxis: x(f.ts),
      name: `${f.kind}: ${fmtDateTime(f.ts)}`,
      // Подпись — только при наведении: соседние отказы иначе налезают друг на друга и на легенду
      label: { show: false, formatter: `${st.label} ${fmtDateTime(f.ts)}`, color: st.color, fontSize: 11 },
      emphasis: { label: { show: true } },
      lineStyle: { color: st.color, type: st.type, width: 2 },
    };
  });
  const hasValues = numeric && h.points.some((p) => p.value_avg != null);
  return {
    animation: false,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { top: 0, type: 'scroll' as const },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [
      { left: 56, right: 56, top: 52, height: '30%' },
      { left: 56, right: 56, top: '56%', height: '32%' },
    ],
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1] }, { type: 'slider', xAxisIndex: [0, 1], height: 18, bottom: 4 }],
    xAxis: [
      { type: 'time', gridIndex: 0, axisLabel: { show: false } },
      { type: 'time', gridIndex: 1 },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, name: 'Событий', nameTextStyle: { align: 'left' } },
      hasValues
        ? { type: 'value', gridIndex: 1, name: 'Значение', scale: true }
        : { type: 'value', gridIndex: 1, name: 'Вероятность', max: 1, axisLabel: { formatter: (v: number) => `${Math.round(v * 100)} %` } },
      { type: 'value', gridIndex: 1, name: 'Вероятность', max: 1, show: hasValues, axisLabel: { formatter: (v: number) => `${Math.round(v * 100)} %` } },
    ],
    series: [
      { name: 'События', type: 'bar', xAxisIndex: 0, yAxisIndex: 0, data: h.points.map((p) => [x(p.t), p.events_count]), itemStyle: { color: '#91caff' }, markLine: { symbol: 'none', data: faults, silent: false } },
      { name: 'Сообщения «Неисправен»', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: h.points.map((p) => [x(p.t), p.fault_count]), itemStyle: { color: RISK_META.critical.color }, showSymbol: false },
      { name: 'Смены статуса', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: h.points.map((p) => [x(p.t), p.status_changes]), itemStyle: { color: '#faad14' }, showSymbol: false },
      ...(hasValues
        ? [
            { name: 'Мин.', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: h.points.map((p) => [x(p.t), p.value_min]), lineStyle: { opacity: 0 }, symbol: 'none', stack: 'band', tooltip: { show: false } },
            {
              name: 'Разброс',
              type: 'line',
              xAxisIndex: 1,
              yAxisIndex: 1,
              data: h.points.map((p) => [x(p.t), p.value_max != null && p.value_min != null ? p.value_max - p.value_min : null]),
              lineStyle: { opacity: 0 },
              areaStyle: { color: 'rgba(22,119,255,0.15)' },
              symbol: 'none',
              stack: 'band',
              tooltip: { show: false },
            },
            { name: 'Среднее значение', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: h.points.map((p) => [x(p.t), p.value_avg]), itemStyle: { color: '#1677ff' }, showSymbol: false, markLine: { symbol: 'none', data: faults } },
          ]
        : []),
      {
        name: 'Вероятность отказа',
        type: 'line',
        step: 'end',
        xAxisIndex: 1,
        yAxisIndex: hasValues ? 2 : 1,
        data: h.predictions.map((p) => [x(p.at), p.prob]),
        tooltip: { valueFormatter: (v: number) => fmtPct(v) },
        itemStyle: { color: RISK_META.risk.color },
        showSymbol: false,
        markLine: hasValues ? undefined : { symbol: 'none', data: faults },
      },
    ],
  };
}

function HistoryBlock({ card }: { card: ChannelCard }) {
  const [at] = useAt();
  const [gran, setGran] = useState<'day' | 'hour'>('day');
  const q = useQuery({
    queryKey: ['history', card.id, at, gran],
    queryFn: () => api.channelHistory(card.id, { at, days: 30, granularity: gran }),
  });
  const numeric = useMemo(() => q.data?.points.some((p) => p.value_avg != null) ?? false, [q.data]);
  return (
    <Card
      title="Активность и значения за 30 дней"
      extra={
        <Segmented
          value={gran}
          onChange={(v) => setGran(v as 'day' | 'hour')}
          options={[
            { value: 'day', label: 'По дням (30 точек)' },
            { value: 'hour', label: 'По часам (до 720)' },
          ]}
        />
      }
    >
      <QueryState query={q} isEmpty={(d) => d.points.length === 0 && d.predictions.length === 0} emptyText="Данных за период нет" skeletonHeight={420}>
        {(d) => (
          <>
            <Chart option={historyOption(d, numeric)} height={440} ariaLabel="График активности, значений и вероятности отказа за 30 дней" />
            <Space size={[12, 4]} wrap>
              <Typography.Text type="secondary">Отказы за период по разметке ML ({d.faults.length}):</Typography.Text>
              {Object.entries(FAULT_KIND_STYLE).map(([kind, st]) => (
                <Typography.Text key={kind} style={{ color: st.color }}>
                  {st.label.split(' ')[0]} {kind} — {d.faults.filter((f) => f.kind === kind).length}
                </Typography.Text>
              ))}
              <Typography.Text type="secondary">Вертикальные линии — моменты отказов (наведите, чтобы увидеть вид и время). Прокрутка колёсиком — масштаб.</Typography.Text>
            </Space>
          </>
        )}
      </QueryState>
    </Card>
  );
}

function PredictionHistory({ card }: { card: ChannelCard }) {
  const [at] = useAt();
  const q = useQuery({
    queryKey: ['history', card.id, at, 'day'],
    queryFn: () => api.channelHistory(card.id, { at, days: 30, granularity: 'day' }),
  });
  return (
    <Card title="История прогнозов">
      <QueryState query={q} isEmpty={(d) => d.predictions.length === 0} emptyText="Прогнозов за период нет">
        {(d) => (
          <Table<PredictionPoint>
            size="small"
            rowKey="prediction_id"
            dataSource={d.predictions.slice().reverse()}
            pagination={{ pageSize: 10, showSizeChanger: false, size: 'small' }}
            columns={[
              { title: 'Срез', dataIndex: 'at', render: (v: string) => fmtDateTime(v) },
              { title: 'Вероятность', dataIndex: 'prob', align: 'right', render: (v: number, r) => <b style={{ color: RISK_META[r.risk_level].color }}>{fmtPct(v)}</b> },
              { title: 'Здоровье', dataIndex: 'health', align: 'right' },
              { title: 'Риск', dataIndex: 'risk_level', render: (l) => <RiskBadge level={l} /> },
            ]}
          />
        )}
      </QueryState>
    </Card>
  );
}

/** Последние события датчика (смены статуса) с контекстом правил: плановая проверка, групповое отключение, сбой */
function ChannelEvents({ card }: { card: ChannelCard }) {
  const [at] = useAt();
  const q = useQuery({
    queryKey: ['events', 'channel', card.id, at],
    queryFn: () =>
      api.events({
        at,
        channel_id: card.id,
        only_alarms: false,
        kind: 'status',
        date_from: dayjs(card.at).subtract(7, 'day').format('YYYY-MM-DDTHH:mm:ss'),
        limit: 50,
      }),
  });
  return (
    <Card title="События за 7 дней">
      <QueryState query={q} isEmpty={(d) => d.total === 0} emptyText="Смен статуса за 7 дней нет" rows={4}>
        {(d) => (
          <Table<EventItem>
            size="small"
            rowKey="id"
            dataSource={d.items}
            pagination={{ pageSize: 8, showSizeChanger: false, size: 'small' }}
            columns={[
              { title: 'Время', dataIndex: 'ts', render: (v: string) => dayjs(v).format('DD.MM HH:mm:ss') },
              {
                title: 'Значение',
                render: (_, r) => (r.is_alarm ? <b style={{ color: RISK_META.critical.color }}>{r.value}</b> : r.value),
              },
              { title: 'Контекст', render: (_, r) => <EventTags item={r} /> },
            ]}
          />
        )}
      </QueryState>
    </Card>
  );
}

export default function ChannelPage() {
  const id = Number(useParams().id);
  const [at] = useAt();
  const q = useQuery({ queryKey: ['channel', id, at], queryFn: () => api.channel(id, at), enabled: Number.isFinite(id) });
  return (
    <QueryState query={q} rows={12}>
      {(card) => (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <div>
            <Breadcrumb
              items={[
                { title: <AtLink to="/objects">Схема объектов</AtLink> },
                ...card.object.path.map((name, i) => ({
                  title: i === card.object.path.length - 1 ? <AtLink to={`/objects?object=${card.object.id}`}>{name}</AtLink> : name,
                })),
                { title: card.name },
              ]}
            />
            <div className="page-title" style={{ marginTop: 8 }}>
              <Space size={12} wrap>
                <Typography.Title level={3}>{card.name}</Typography.Title>
                <RiskBadge level={card.prediction?.risk_level} />
                {card.on_watch && (
                  <Tag icon={<EyeOutlined />} color="blue">
                    На контроле
                  </Tag>
                )}
              </Space>
            </div>
            <Descriptions size="small" column={{ xs: 1, md: 2, xl: 5 }} bordered style={{ background: 'var(--app-surface)' }}>
              <Descriptions.Item label="Тип">{card.sensor_type}</Descriptions.Item>
              <Descriptions.Item label="Система">{card.system_type}</Descriptions.Item>
              <Descriptions.Item label="Объект">
                <Tooltip title={card.object.path.join(' → ')}>{card.object.name}</Tooltip>
              </Descriptions.Item>
              <Descriptions.Item label="Тег">
                <Tooltip title={`Уровни: ${card.tag_levels.join(' / ')}`}>
                  <Typography.Text code copyable>
                    {card.tag}
                  </Typography.Text>
                </Tooltip>
              </Descriptions.Item>
              <Descriptions.Item label="Последнее событие">
                {card.last_event ? (
                  <Space size={4}>
                    <span>{fmtDateTime(card.last_event.ts)}</span>
                    <Tag color={card.last_event.is_alarm ? 'red' : 'default'}>
                      {card.last_event.value_num != null ? fmtNum(card.last_event.value_num, 3) : card.last_event.value}
                    </Tag>
                  </Space>
                ) : (
                  '—'
                )}
              </Descriptions.Item>
            </Descriptions>
          </div>
          <PredictionBlock card={card} />
          <RecommendationCard card={card} />
          <Row gutter={[16, 16]}>
            <Col xs={24} xxl={16}>
              <HistoryBlock card={card} />
            </Col>
            <Col xs={24} xxl={8}>
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                <PredictionHistory card={card} />
                <ChannelEvents card={card} />
              </Space>
            </Col>
          </Row>
        </Space>
      )}
    </QueryState>
  );
}
