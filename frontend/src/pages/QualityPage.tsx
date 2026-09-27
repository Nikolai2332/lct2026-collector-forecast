import { QuestionCircleOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { Alert, Card, Col, DatePicker, Progress, Row, Space, Statistic, Table, Tooltip, Typography } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { useMemo, useState } from 'react';
import { api } from '@/api/endpoints';
import { Chart } from '@/components/Chart';
import { QueryState } from '@/components/QueryState';
import { useDataMoment } from '@/hooks/useSnapshot';
import type { ModelMetrics } from '@/types';
import { RISK_META } from '@/utils/risk';
import { METRIC_HINTS } from '@/utils/metrics';
import { fmtDate, fmtNum, fmtPct, fmtThreshold, fmtThresholdExact, round1 } from '@/utils/time';

type SensorRow = ModelMetrics['by_sensor_type'][number];
type CompareRow = NonNullable<ModelMetrics['comparison']>[number];
type GroupRow = NonNullable<ModelMetrics['by_group']>[number];
type KindRow = NonNullable<ModelMetrics['recall_by_kind']>[number];

const LABEL_NAMES: Record<string, string> = { b: 'новая метка (v3)', v2: 'старая метка (v2)' };

function MetricCard({ title, value, baseline, hint }: { title: string; value?: number | null; baseline?: number | null; hint: string }) {
  return (
    <Card size="small">
      <Tooltip title={hint}>
        <Statistic
          title={
            <span className="metric-title">
              {title} <QuestionCircleOutlined />
            </span>
          }
          value={value != null ? round1(value * 100) : '—'}
          precision={1}
          decimalSeparator=","
          suffix={value != null ? '%' : undefined}
        />
      </Tooltip>
      <Tooltip title={METRIC_HINTS.baseline()}>
        <Typography.Text type="secondary">{baseline != null ? <>Базовое правило: {fmtPct(baseline, 1)}</> : ' '}</Typography.Text>
      </Tooltip>
    </Card>
  );
}

function prOption(m: ModelMetrics) {
  const pts = [...m.pr_curve].sort((a, b) => a.recall - b.recall);
  const sel = m.pr_curve.find((r) => r.threshold === m.threshold);
  return {
    animation: false,
    tooltip: {
      trigger: 'item',
      formatter: (p: { data: [number, number, number] }) =>
        `Порог тревоги ${fmtThreshold(p.data[2])}<br/>Recall (полнота) ${fmtPct(p.data[0], 1)}<br/>Precision (точность) ${fmtPct(p.data[1], 1)}`,
    },
    grid: { left: 56, right: 24, top: 24, bottom: 48 },
    xAxis: { type: 'value', name: 'Recall (полнота)', nameLocation: 'middle', nameGap: 28, min: 0, max: 1, axisLabel: { formatter: (v: number) => `${Math.round(v * 100)} %` } },
    yAxis: { type: 'value', name: 'Precision (точность)', min: 0, max: 1, axisLabel: { formatter: (v: number) => `${Math.round(v * 100)} %` } },
    series: [
      {
        type: 'line',
        data: pts.map((r) => [r.recall, r.precision, r.threshold]),
        symbolSize: 6,
        itemStyle: { color: '#1677ff' },
        areaStyle: { color: 'rgba(22,119,255,0.08)' },
        markPoint: sel
          ? {
              symbol: 'pin',
              symbolSize: 46,
              itemStyle: { color: RISK_META.risk.color },
              label: { formatter: fmtThreshold(sel.threshold), color: '#fff', fontSize: 11 },
              data: [{ coord: [sel.recall, sel.precision], name: 'Рабочий порог тревоги' }],
            }
          : undefined,
      },
    ],
  };
}

function dailyOption(m: ModelMetrics) {
  const d = m.daily;
  return {
    animation: false,
    tooltip: { trigger: 'axis' },
    legend: { top: 0 },
    grid: { left: 48, right: 24, top: 36, bottom: 56 },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 6 }],
    xAxis: { type: 'category', data: d.map((x) => dayjs(x.date).format('DD.MM')) },
    yAxis: { type: 'value', name: 'Датчиков', axisLabel: { formatter: (v: number) => fmtNum(v) } },
    series: [
      { name: 'Предсказано отказов', type: 'bar', data: d.map((x) => x.predicted), itemStyle: { color: '#ffc069' } },
      { name: 'Фактически отказов', type: 'bar', data: d.map((x) => x.actual), itemStyle: { color: '#ff7875' } },
      { name: 'Предсказано и случилось', type: 'line', data: d.map((x) => x.true_positive), itemStyle: { color: '#389e0d' }, symbolSize: 5 },
    ],
  };
}

const pctBar = (v: number | null | undefined) =>
  v == null ? (
    '—'
  ) : (
    <Space size={6}>
      <Progress percent={Math.round(v * 100)} size="small" showInfo={false} style={{ width: 80, margin: 0 }} />
      <span>{fmtPct(v, 1)}</span>
    </Space>
  );

export default function QualityPage() {
  const { at, moment, stale, snapshot, ready } = useDataMoment();
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const effective = useMemo<[Dayjs, Dayjs]>(() => range ?? [moment.subtract(30, 'day').startOf('day'), moment.startOf('day')], [range, moment]);
  const q = useQuery({
    queryKey: ['metrics', at, effective[0].format('YYYY-MM-DD'), effective[1].format('YYYY-MM-DD')],
    queryFn: () => api.metrics({ at, date_from: effective[0].format('YYYY-MM-DD'), date_to: effective[1].format('YYYY-MM-DD') }),
    enabled: ready,
  });

  return (
    <>
      <div className="page-title">
        <Typography.Title level={3}>Качество модели</Typography.Title>
        <Space wrap>
          <span>Период для графика по дням:</span>
          <DatePicker.RangePicker
            format="DD.MM.YYYY"
            value={effective}
            allowClear={false}
            onChange={(v) => v && setRange(v as [Dayjs, Dayjs])}
            disabledDate={(d) => !!range && Math.abs(d.diff(range[0], 'day')) > 92}
            presets={[
              { label: '7 дней', value: [moment.subtract(7, 'day').startOf('day'), moment.startOf('day')] },
              { label: '30 дней', value: [moment.subtract(30, 'day').startOf('day'), moment.startOf('day')] },
              { label: '90 дней', value: [moment.subtract(90, 'day').startOf('day'), moment.startOf('day')] },
            ]}
          />
        </Space>
      </div>
      {stale && !range && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }} message={`Последний срез прогнозов — ${fmtDate(snapshot)}; период по умолчанию — 30 дней до него.`} />
      )}
      <QueryState query={{ ...q, isPending: q.isPending || !ready }} rows={12} isEmpty={(m) => !m.overall && m.pr_curve.length === 0} emptyText="Метрик модели пока нет">
        {(m) => (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Typography.Text type="secondary">
              Модель {m.model_version ?? '—'} ·{' '}
              <Tooltip title={METRIC_HINTS.threshold(fmtThresholdExact(m.threshold))}>
                <span className="metric-title">
                  порог тревоги {fmtThreshold(m.threshold)} <QuestionCircleOutlined />
                </span>
              </Tooltip>{' '}
              · метрики за весь тестовый период {fmtDate(m.test_period_from)} — {fmtDate(m.test_period_to)}. «Точность тревог за
              последние 30 дней» на дашборде считается за другой, короткий период, поэтому число там другое.
            </Typography.Text>
            <Row gutter={[16, 16]}>
              <Col xs={12} xl={4}>
                <MetricCard title="Precision (точность)" value={m.overall?.precision} baseline={m.baseline?.precision} hint={METRIC_HINTS.precision(m.overall?.precision)} />
              </Col>
              <Col xs={12} xl={4}>
                <MetricCard title="Recall (полнота)" value={m.overall?.recall} baseline={m.baseline?.recall} hint={METRIC_HINTS.recall(m.overall?.recall)} />
              </Col>
              <Col xs={12} xl={4}>
                <MetricCard title="PR-AUC" value={m.overall?.pr_auc} baseline={m.baseline?.pr_auc} hint={METRIC_HINTS.prAuc()} />
              </Col>
              <Col xs={12} xl={4}>
                <MetricCard title="F1" value={m.overall?.f1} baseline={m.baseline?.f1} hint={METRIC_HINTS.f1()} />
              </Col>
              <Col xs={12} xl={4}>
                <Card size="small">
                  <Tooltip title={METRIC_HINTS.lead()}>
                    <Statistic
                      title={
                        <span className="metric-title">
                          Медианное упреждение <QuestionCircleOutlined />
                        </span>
                      }
                      value={m.median_lead_time_h != null ? round1(m.median_lead_time_h) : '—'}
                      precision={m.median_lead_time_h != null ? 1 : undefined}
                      decimalSeparator=","
                      suffix={m.median_lead_time_h != null ? 'ч' : undefined}
                    />
                  </Tooltip>
                  <Typography.Text type="secondary">от тревоги до отказа</Typography.Text>
                </Card>
              </Col>
              <Col xs={12} xl={4}>
                <Card size="small">
                  <Typography.Text type="secondary">Точность в топе за сутки (P@K)</Typography.Text>
                  {m.precision_at_k.map((p) => (
                    <Tooltip key={p.k} title={METRIC_HINTS.pAtK(p.k, p.precision)} placement="left">
                      <div style={{ display: 'flex', justifyContent: 'space-between', cursor: 'help' }}>
                        <span>топ-{p.k}</span>
                        <b>{fmtPct(p.precision, 1)}</b>
                      </div>
                    </Tooltip>
                  ))}
                </Card>
              </Col>
            </Row>
            {m.label_note && <Alert type="info" showIcon message="Почему изменилась метка отказа" description={m.label_note} />}
            {!!m.comparison?.length && (
              <Card size="small" title="Сравнение с v2 на одной и той же метке (тестовый период)">
                <Table<CompareRow>
                  size="small"
                  rowKey={(r) => `${r.model}@${r.label}`}
                  dataSource={m.comparison}
                  pagination={false}
                  scroll={{ x: 700 }}
                  columns={[
                    { title: 'Метка', dataIndex: 'label', render: (v: string) => LABEL_NAMES[v] ?? v },
                    { title: 'Модель', dataIndex: 'model', width: 90 },
                    { title: 'Precision (точность)', dataIndex: 'precision', width: 180, render: pctBar },
                    { title: 'Recall (полнота)', dataIndex: 'recall', width: 180, render: pctBar },
                    { title: 'F1', dataIndex: 'f1', width: 90, align: 'right', render: (v) => fmtPct(v, 1) },
                    { title: 'PR-AUC', dataIndex: 'pr_auc', width: 90, align: 'right', render: (v) => fmtPct(v, 1) },
                    { title: 'Тревог в сутки', dataIndex: 'alerts_per_day', width: 130, align: 'right', render: (v) => (v != null ? fmtNum(Math.round(v)) : '—') },
                  ]}
                />
              </Card>
            )}
            {(!!m.by_group?.length || !!m.recall_by_kind?.length) && (
              <Row gutter={[16, 16]}>
                <Col xs={24} xl={14}>
                  <Card size="small" title="По группам датчиков заказчика (приоритет: газовые, пожарные, охранные)">
                    <Table<GroupRow>
                      size="small"
                      rowKey="group"
                      dataSource={m.by_group ?? []}
                      pagination={false}
                      scroll={{ x: 600 }}
                      columns={[
                        { title: 'Группа', dataIndex: 'group' },
                        { title: 'Отказов в тесте', dataIndex: 'support', align: 'right', width: 120, render: (v) => fmtNum(v) },
                        { title: 'Precision', dataIndex: 'precision', width: 150, render: pctBar },
                        { title: 'Recall', dataIndex: 'recall', width: 150, render: pctBar },
                        { title: 'PR-AUC', dataIndex: 'pr_auc', width: 90, align: 'right', render: (v) => fmtPct(v, 1) },
                      ]}
                    />
                  </Card>
                </Col>
                <Col xs={24} xl={10}>
                  <Card size="small" title="Recall по видам отказа">
                    <Table<KindRow>
                      size="small"
                      rowKey="kind"
                      dataSource={m.recall_by_kind ?? []}
                      pagination={false}
                      columns={[
                        { title: 'Вид отказа', dataIndex: 'kind' },
                        { title: 'Отказов', dataIndex: 'support', align: 'right', width: 100, render: (v) => fmtNum(v) },
                        { title: 'Recall', dataIndex: 'recall', width: 150, render: pctBar },
                      ]}
                    />
                  </Card>
                </Col>
              </Row>
            )}
            <Row gutter={[16, 16]}>
              <Col xs={24} xl={10}>
                <Card size="small" title="Точность и полнота при разных порогах (PR-кривая)">
                  <Chart option={prOption(m)} height={340} ariaLabel="PR-кривая модели с отмеченным рабочим порогом тревоги" />
                </Card>
              </Col>
              <Col xs={24} xl={14}>
                <Card size="small" title={`Прогноз против факта по дням: ${fmtDate(m.date_from)} — ${fmtDate(m.date_to)}`}>
                  {m.daily.length ? (
                    <Chart option={dailyOption(m)} height={340} ariaLabel="Сравнение предсказанных и фактических отказов по дням" />
                  ) : (
                    <Typography.Paragraph type="secondary" style={{ padding: 32, textAlign: 'center' }}>
                      За выбранный период данных нет
                    </Typography.Paragraph>
                  )}
                </Card>
              </Col>
            </Row>
            <Card size="small" title="Метрики по типам датчиков">
              <Table<SensorRow>
                size="small"
                rowKey="sensor_type"
                dataSource={m.by_sensor_type}
                pagination={false}
                scroll={{ x: 700 }}
                columns={[
                  { title: 'Тип датчика', dataIndex: 'sensor_type' },
                  { title: 'Отказов в тесте', dataIndex: 'support', align: 'right', width: 130, render: (v) => fmtNum(v), sorter: (a, b) => (a.support ?? 0) - (b.support ?? 0) },
                  { title: 'Precision (точность)', dataIndex: 'precision', width: 180, render: pctBar, sorter: (a, b) => (a.precision ?? 0) - (b.precision ?? 0) },
                  { title: 'Recall (полнота)', dataIndex: 'recall', width: 180, render: pctBar, sorter: (a, b) => (a.recall ?? 0) - (b.recall ?? 0) },
                  { title: 'F1', dataIndex: 'f1', width: 100, align: 'right', render: (v) => fmtPct(v, 1) },
                  { title: 'PR-AUC', dataIndex: 'pr_auc', width: 100, align: 'right', render: (v) => fmtPct(v, 1) },
                ]}
              />
            </Card>
          </Space>
        )}
      </QueryState>
    </>
  );
}
