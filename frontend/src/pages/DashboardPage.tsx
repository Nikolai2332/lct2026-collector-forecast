import {
  AlertOutlined,
  AimOutlined,
  FileTextOutlined,
  FundOutlined,
  NotificationOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, Col, Row, Slider, Space, Statistic, Table, Tag, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import { useEffect, useMemo, useState } from 'react';
import { api } from '@/api/endpoints';
import { useThresholds } from '@/api/queries';
import { AtLink, ChannelLink, DecisionTag, ProbText, WorkOrderTag } from '@/components/cells';
import { DecisionModal, type DecisionTarget } from '@/components/DecisionModal';
import { MainFactor } from '@/components/Factors';
import { ObjectTiles } from '@/components/ObjectTiles';
import { QueryState } from '@/components/QueryState';
import { RiskBadge } from '@/components/RiskBadge';
import { WorkOrderModal, type WorkOrderSource } from '@/components/WorkOrderModal';
import { useAt, useAtNavigate } from '@/hooks/useAt';
import { useAuth } from '@/hooks/useAuth';
import type { PredictionItem, RiskLevel } from '@/types';
import { sliceJournalPath } from '@/utils/notifications';
import { plural, sensorsWord } from '@/utils/plural';
import { RISK_LEVELS, RISK_META } from '@/utils/risk';
import { METRIC_HINTS } from '@/utils/metrics';
import { fmtDate, fmtDateTime, fmtNum, fmtPct, fmtThreshold, fmtThresholdExact, round1 } from '@/utils/time';

function SummaryCards() {
  const [at] = useAt();
  const q = useQuery({ queryKey: ['summary', at], queryFn: () => api.summary(at) });
  return (
    <QueryState query={q} rows={2}>
      {(s) => (
        <Row gutter={[16, 16]}>
          <Col xs={24} sm={12} xl={6}>
            <Card size="small">
              <Statistic
                title="Каналы в зоне риска"
                value={s.channels_at_risk}
                prefix={<AlertOutlined style={{ color: RISK_META.critical.color }} />}
                suffix={<Typography.Text type="secondary" style={{ fontSize: 14 }}>из {fmtNum(s.channels_total)}</Typography.Text>}
              />
              <Space size={4} wrap style={{ marginTop: 4 }}>
                {RISK_LEVELS.map((l) => (
                  <RiskBadge key={l} level={l} suffix={fmtNum(s.risk_distribution[l] ?? 0)} />
                ))}
              </Space>
            </Card>
          </Col>
          <Col xs={24} sm={12} xl={6}>
            <Card size="small">
              <Statistic title="Прогнозы отказов на 24 ч" value={s.predicted_failures_24h} prefix={<ThunderboltOutlined style={{ color: '#d46b08' }} />} />
              <Typography.Text type="secondary">
                Ожидаемое число отказов: {fmtNum(s.expected_failures_24h, 1)}
                <br />
                Срез прогноза: {fmtDateTime(s.snapshot_at)}
              </Typography.Text>
            </Card>
          </Col>
          <Col xs={24} sm={12} xl={6}>
            <Card size="small">
              <Statistic title="Открытые заявки" value={s.open_work_orders} prefix={<FileTextOutlined style={{ color: '#1677ff' }} />} />
              <AtLink to="/work-orders">Перейти к заявкам</AtLink>
            </Card>
          </Col>
          <Col xs={24} sm={12} xl={6}>
            <Card size="small">
              <Tooltip
                title={
                  <>
                    <div>{METRIC_HINTS.precision(s.accuracy_30d.precision)}</div>
                    <div>{METRIC_HINTS.recall(s.accuracy_30d.recall)}</div>
                    <div style={{ marginTop: 6 }}>
                      Считается по прогнозам с уже известным исходом за 30 дней до момента ({fmtDate(s.accuracy_30d.window_from)} — {fmtDate(s.accuracy_30d.window_to)}), все срезы, порог тревоги {fmtThreshold(s.accuracy_30d.threshold)}.
                      На экране «Качество модели» — официальный результат за весь тестовый период 2026 г. по суточным срезам, поэтому числа отличаются.
                    </div>
                  </>
                }
              >
                <Statistic
                  title="Точность тревог за последние 30 дней"
                  value={s.accuracy_30d.precision != null ? round1(s.accuracy_30d.precision * 100) : '—'}
                  precision={1}
                  decimalSeparator=","
                  suffix={s.accuracy_30d.precision != null ? '%' : undefined}
                  prefix={<AimOutlined style={{ color: '#389e0d' }} />}
                />
                <Typography.Text type="secondary">
                  Precision {fmtPct(s.accuracy_30d.precision, 1)} · Recall {fmtPct(s.accuracy_30d.recall, 1)}
                  <br />
                  <span style={{ fontSize: 12 }}>не весь тест — см. «Качество модели»</span>
                </Typography.Text>
              </Tooltip>
            </Card>
          </Col>
        </Row>
      )}
    </QueryState>
  );
}

function TopRisks({ onDecide, onWorkOrder }: { onDecide: (t: DecisionTarget) => void; onWorkOrder: (s: WorkOrderSource) => void }) {
  const [at] = useAt();
  const { canAct } = useAuth();
  const q = useQuery({ queryKey: ['predictions', at, 'top20'], queryFn: () => api.predictions({ at, limit: 20 }) });
  const columns: ColumnsType<PredictionItem> = [
    { title: 'Риск', dataIndex: 'risk_level', width: 120, render: (l: RiskLevel) => <RiskBadge level={l} /> },
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
    { title: 'Объект', render: (_, r) => <Tooltip title={r.object.path.join(' → ')}>{r.object.name}</Tooltip>, responsive: ['lg'] },
    { title: 'Вероятность', dataIndex: 'prob', width: 110, align: 'right', render: (p: number, r) => <ProbText prob={p} level={r.risk_level} /> },
    {
      title: 'Главная причина',
      key: 'factor',
      width: 250,
      responsive: ['xl'],
      render: (_, r) => <MainFactor p={r} />,
    },
    { title: 'Решение', dataIndex: 'decision', width: 150, render: (d) => <DecisionTag decision={d} /> },
    {
      title: 'Действия',
      width: 210,
      render: (_, r) => (
        <Space size={4}>
          {r.work_order_id ? (
            <WorkOrderTag id={r.work_order_id} />
          ) : (
            canAct && (
              <Button size="small" type="primary" onClick={() => onWorkOrder({ ...r, prediction_id: r.prediction_id })}>
                Создать заявку
              </Button>
            )
          )}
          {canAct && (
            <Button
              size="small"
              onClick={() =>
                onDecide({ prediction_id: r.prediction_id, channelName: r.channel.name, objectName: r.object.name, prob: r.prob, risk_level: r.risk_level })
              }
            >
              Решение
            </Button>
          )}
        </Space>
      ),
    },
  ];
  return (
    <Card size="small" title="Топ-20 датчиков по риску" extra={<AtLink to="/journal">Журнал прогнозов</AtLink>}>
      <QueryState query={q} isEmpty={(d) => d.items.length === 0} rows={10}>
        {(d) => (
          <Table
            size="small"
            rowKey="prediction_id"
            columns={columns}
            dataSource={d.items}
            pagination={false}
            rowClassName={(r) => `row-${r.risk_level}`}
            scroll={{ x: 760 }}
          />
        )}
      </QueryState>
    </Card>
  );
}

function ThresholdCard() {
  const q = useThresholds();
  const [value, setValue] = useState<number | null>(null);
  useEffect(() => {
    if (q.data) setValue((v) => v ?? q.data.selected_threshold ?? q.data.items[Math.floor(q.data.items.length / 2)]?.threshold ?? null);
  }, [q.data]);
  const rows = useMemo(() => q.data?.items ?? [], [q.data]);
  const row = useMemo(
    () => rows.reduce<(typeof rows)[number] | undefined>((best, r) => (!best || Math.abs(r.threshold - (value ?? 0)) < Math.abs(best.threshold - (value ?? 0)) ? r : best), undefined),
    [rows, value],
  );
  const faultsPerDay = row && row.recall > 0 ? row.tp_per_day / row.recall : null;
  return (
    <Card
      size="small"
      title={
        <Tooltip title={METRIC_HINTS.threshold(fmtThresholdExact(q.data?.selected_threshold))}>
          <span>
            <FundOutlined /> Чувствительность тревог
          </span>
        </Tooltip>
      }
    >
      <QueryState query={q} isEmpty={(d) => d.items.length === 0} emptyText="Таблица порогов ещё не загружена" rows={3} compact>
        {(d) => (
          <>
            <Slider
              min={d.items[0].threshold}
              max={d.items[d.items.length - 1].threshold}
              step={null}
              marks={Object.fromEntries(
                d.items.map((r) => [
                  r.threshold,
                  r.threshold === d.selected_threshold
                    ? { label: <b>{fmtThreshold(r.threshold)}</b>, style: { color: '#1677ff' } }
                    : // подписи кратных 0.2 порогов, кроме соседних с рабочим — иначе они налезают на его подпись
                      Math.round(r.threshold * 100) % 20 === 0 && (d.selected_threshold == null || Math.abs(r.threshold - d.selected_threshold) > 0.07)
                      ? fmtThreshold(r.threshold).replace(/0$/, '')
                      : ' ',
                ]),
              )}
              value={value ?? undefined}
              onChange={setValue}
              tooltip={{ formatter: (v) => `порог тревоги ${fmtThreshold(v)}` }}
              aria-label="Порог тревоги"
            />
            {row && (
              <Row gutter={8}>
                <Col span={12}>
                  <Statistic
                    title="Поймаем отказов в сутки"
                    value={round1(row.tp_per_day)}
                    precision={1}
                decimalSeparator=","
                    valueStyle={{ color: '#389e0d' }}
                    suffix={faultsPerDay ? <span style={{ fontSize: 13 }}>из {fmtNum(faultsPerDay, 1)} ({fmtPct(row.recall)})</span> : undefined}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title="Лишних выездов в сутки"
                    value={round1(row.fp_per_day)}
                    precision={1}
                decimalSeparator=","
                    valueStyle={{ color: '#d46b08' }}
                    suffix={<span style={{ fontSize: 13 }}>всего тревог {fmtNum(row.alerts_per_day, 1)}</span>}
                  />
                </Col>
              </Row>
            )}
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              <Tooltip title={`Точное значение: ${fmtThresholdExact(d.selected_threshold)}`}>
                <span>Рабочий порог тревоги модели — {fmtThreshold(d.selected_threshold)}.</span>
              </Tooltip>{' '}
              Левее — больше пойманных отказов и больше лишних выездов. Ползунок только пересчитывает оценку, настройки модели не меняет.
            </Typography.Text>
          </>
        )}
      </QueryState>
    </Card>
  );
}

function MiniSchema() {
  const [at] = useAt();
  const nav = useAtNavigate();
  const q = useQuery({ queryKey: ['objects', at, null, null], queryFn: () => api.objects({ at }) });
  return (
    <Card size="small" title="Схема объектов" extra={<AtLink to="/objects">Подробнее</AtLink>}>
      <QueryState query={q} isEmpty={(d) => d.items.length === 0} emptyText="Объектов нет" skeletonHeight={180}>
        {(d) => (
          <ObjectTiles
            compact
            minWidth={110}
            nodes={d.items.flatMap((root) => root.children ?? [])}
            onSelect={(n) => nav(`/objects?object=${n.id}`)}
          />
        )}
      </QueryState>
    </Card>
  );
}

function NotificationFeed() {
  const [at] = useAt();
  // Бэкенд считает ленту от последнего среза не позже at — «сейчас» после конца данных не пустое
  const q = useQuery({ queryKey: ['notifications', at], queryFn: () => api.notifications({ at, hours: 24, limit: 100 }) });
  return (
    <Card
      size="small"
      title={
        <Tooltip title="Датчик перешёл в «Критично» и не был в нём сутки до этого. Повторные часы того же датчика не показываются.">
          <span>
            <NotificationOutlined /> Новые критичные за 24 ч
          </span>
        </Tooltip>
      }
      extra={q.data && <Tag>{sensorsWord(q.data.total)}</Tag>}
    >
      <QueryState query={q} isEmpty={(d) => d.total === 0} emptyText="Новых критичных за сутки нет" rows={4} compact>
        {(d) => (
          <div style={{ maxHeight: 360, overflow: 'auto' }}>
            {(d.groups ?? []).map((g) => {
              const shown = d.items.filter((n) => n.created_at === g.snapshot_at).slice(0, 3);
              const rest = g.count - shown.length;
              return (
                <div key={g.snapshot_at} className="feed-group">
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {fmtDateTime(g.snapshot_at)} · {sensorsWord(g.count)}
                  </Typography.Text>
                  {shown.map((n) => (
                    <div key={n.id} className="feed-item">
                      <RiskBadge level={n.prediction.risk_level} iconOnly />
                      <div style={{ minWidth: 0 }}>
                        <ChannelLink id={n.prediction.channel.id} name={n.prediction.channel.name} />
                        <Typography.Text type="secondary"> · {n.prediction.object.name} · {fmtPct(n.prediction.prob)}</Typography.Text>
                        <div style={{ fontSize: 12 }}>
                          <MainFactor p={n.prediction} />
                        </div>
                      </div>
                    </div>
                  ))}
                  {rest > 0 && (
                    <AtLink to={sliceJournalPath(g.snapshot_at)}>
                      {shown.length ? 'ещё ' : ''}
                      {sensorsWord(rest)} {plural(rest, 'перешёл', 'перешли', 'перешли')} в «Критично» в {dayjs(g.snapshot_at).format('HH:mm')} →
                    </AtLink>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </QueryState>
    </Card>
  );
}

export default function DashboardPage() {
  const [decision, setDecision] = useState<DecisionTarget | null>(null);
  const [woSource, setWoSource] = useState<WorkOrderSource | null>(null);
  return (
    <>
      <div className="page-title">
        <Typography.Title level={3}>Дашборд</Typography.Title>
      </div>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <SummaryCards />
        <Row gutter={[16, 16]}>
          {/* До 1600 px таблице нужна вся ширина: карточки справа уходят под неё в один ряд */}
          <Col xs={24} xxl={16}>
            <TopRisks onDecide={setDecision} onWorkOrder={setWoSource} />
          </Col>
          <Col xs={24} xxl={8}>
            <Row gutter={[16, 16]}>
              <Col xs={24} lg={12} xl={8} xxl={24}>
                <ThresholdCard />
              </Col>
              <Col xs={24} lg={12} xl={8} xxl={24}>
                <MiniSchema />
              </Col>
              <Col xs={24} xl={8} xxl={24}>
                <NotificationFeed />
              </Col>
            </Row>
          </Col>
        </Row>
      </Space>
      <DecisionModal target={decision} onClose={() => setDecision(null)} />
      <WorkOrderModal open={!!woSource} source={woSource} onClose={() => setWoSource(null)} />
    </>
  );
}
