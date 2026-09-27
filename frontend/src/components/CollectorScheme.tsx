import { AimOutlined, ExpandOutlined } from '@ant-design/icons';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Checkbox, Col, Empty, List, Row, Space, Tag, Tooltip, Typography } from 'antd';
import type * as echarts from 'echarts/core';
import { useCallback, useMemo, useRef, useState } from 'react';
import { api } from '@/api/endpoints';
import { Chart } from '@/components/Chart';
import { QueryState } from '@/components/QueryState';
import { useAt, useAtNavigate } from '@/hooks/useAt';
import { useThemeMode } from '@/hooks/useThemeMode';
import type { RiskLevel } from '@/types';
import { RISK_LEVELS, RISK_META } from '@/utils/risk';
import { fmtDateTime, fmtNum } from '@/utils/time';

/** Свойства объектов из /api/geo (GeoJSON): геометрия синтетическая, условные метры, 1 ПК = 10 м */
interface CollectorProps {
  kind: 'collector' | 'section' | 'node' | 'problem_zone';
  object_id: number;
  parent_id?: number;
  name: string;
  path?: string[];
  length_m?: number;
  galleries?: number;
  channels?: number;
  critical?: number;
  max_prob?: number;
  channel_ids?: number[];
  max_risk_level?: RiskLevel | null;
}
interface ChannelProps {
  channel_id: number;
  name: string;
  sensor_type: string;
  system_type: string;
  object_id: number;
  risk_level?: RiskLevel;
  prob?: number;
  picket?: string;
  placed_by: 'picket' | 'gallery' | 'node';
  side?: 'left' | 'right';
  feature?: string;
}
interface Feature<P> {
  geometry: { type: string; coordinates: number[] | number[][] | number[][][] };
  properties: P;
}

/** Уровень риска дублируется формой: круг — норма, треугольник — внимание, квадрат — риск, ромб — критично */
const SYMBOL: Record<RiskLevel, string> = { normal: 'circle', attention: 'triangle', risk: 'rect', critical: 'diamond' };
const SIZE: Record<RiskLevel, number> = { normal: 5, attention: 7, risk: 9, critical: 12 };
const PLACED: Record<ChannelProps['placed_by'], string> = {
  picket: 'по пикету на основном теле',
  gallery: 'по пикету в галерее',
  node: 'в узле объекта (пикета в названии нет)',
};

/** Линии через null-разрывы — одна серия на все отрезки (быстрее сотни серий) */
function polyline(lines: number[][][]): (number[] | null)[] {
  const out: (number[] | null)[] = [];
  for (const l of lines) {
    out.push(...l, null);
  }
  return out;
}

interface Props {
  system?: string;
  sensor?: string;
  objectId?: number | null;
}

export function CollectorScheme({ system, sensor, objectId }: Props) {
  const [at] = useAt();
  const nav = useAtNavigate();
  const chartRef = useRef<echarts.ECharts | null>(null);
  const { mode } = useThemeMode();
  const dark = mode === 'dark';
  const [levels, setLevels] = useState<RiskLevel[]>(['critical', 'risk', 'attention', 'normal']);
  const [showZones, setShowZones] = useState(true);
  const cols = useQuery({
    queryKey: ['geoCollectors', at, objectId ?? null],
    queryFn: () => api.geoCollectors({ at, object_id: objectId ?? undefined }),
    placeholderData: keepPreviousData,
  });
  const pts = useQuery({
    queryKey: ['geoChannels', at, objectId ?? null, system ?? null, sensor ?? null],
    queryFn: () => api.geoChannels({ at, object_id: objectId ?? undefined, system_type: system, sensor_type: sensor }),
    placeholderData: keepPreviousData,
  });

  const collectors = useMemo(
    () => ((cols.data?.features ?? []) as unknown as Feature<CollectorProps>[]),
    [cols.data],
  );
  const channels = useMemo(() => ((pts.data?.features ?? []) as unknown as Feature<ChannelProps>[]), [pts.data]);
  const objectName = useMemo(() => {
    const m = new Map<number, string>();
    for (const f of collectors) if (f.properties.kind !== 'problem_zone') m.set(f.properties.object_id, f.properties.name);
    return m;
  }, [collectors]);
  const zones = useMemo(
    () =>
      collectors
        .filter((f) => f.properties.kind === 'problem_zone')
        .sort((a, b) => (b.properties.critical ?? 0) - (a.properties.critical ?? 0) || (b.properties.channels ?? 0) - (a.properties.channels ?? 0)),
    [collectors],
  );

  const option = useMemo(() => {
    const byKind = (k: CollectorProps['kind']) => collectors.filter((f) => f.properties.kind === k);
    const axes = byKind('collector');
    const mainLines = polyline(axes.flatMap((f) => f.geometry.coordinates as number[][][]));
    const sectionLines = polyline(byKind('section').map((f) => f.geometry.coordinates as number[][]));
    const zoneLines = polyline(zones.map((f) => f.geometry.coordinates as number[][]));
    const labels = axes.map((f) => {
      const [[x, y]] = (f.geometry.coordinates as number[][][])[0];
      const lvl = f.properties.max_risk_level;
      return { value: [x, y], name: f.properties.name, lvl };
    });
    const series: object[] = [
      {
        name: 'Основное тело и галереи',
        type: 'line',
        data: mainLines,
        connectNulls: false,
        showSymbol: false,
        silent: true,
        lineStyle: { width: 6, color: '#8c8c8c', opacity: dark ? 0.7 : 0.55, cap: 'round' },
        z: 1,
      },
      {
        name: 'Участки подобъектов',
        type: 'line',
        data: sectionLines,
        connectNulls: false,
        showSymbol: false,
        silent: true,
        lineStyle: { width: 2, color: dark ? '#4096ff' : '#1677ff', opacity: dark ? 0.5 : 0.35 },
        z: 1,
      },
      {
        name: 'Объекты',
        type: 'scatter',
        data: labels,
        symbolSize: 1,
        silent: true,
        label: {
          show: true,
          position: 'left',
          formatter: (p: { name: string }) => p.name,
          fontWeight: 600,
          color: dark ? '#e6e6e6' : '#262626',
          distance: 70,
        },
        z: 3,
      },
    ];
    if (showZones) {
      series.push({
        name: 'Проблемные участки',
        type: 'line',
        data: zoneLines,
        connectNulls: false,
        showSymbol: false,
        silent: true,
        lineStyle: { width: 18, color: RISK_META.critical.color, opacity: 0.22, cap: 'round' },
        z: 2,
      });
    }
    for (const lvl of [...RISK_LEVELS].reverse()) {
      if (!levels.includes(lvl)) continue;
      const data = channels
        .filter((f) => (f.properties.risk_level ?? 'normal') === lvl)
        .map((f) => ({ value: f.geometry.coordinates as number[], props: f.properties }));
      series.push({
        name: RISK_META[lvl].label,
        type: 'scatter',
        data,
        symbol: SYMBOL[lvl],
        symbolSize: SIZE[lvl],
        itemStyle: { color: RISK_META[lvl].color, borderColor: dark ? '#141414' : '#fff', borderWidth: lvl === 'normal' ? 0 : 1 },
        emphasis: { scale: 1.8 },
        large: lvl === 'normal' && data.length > 3000,
        z: 4 + RISK_META[lvl].order,
      });
    }
    return {
      animation: false,
      grid: { left: 180, right: 24, top: 16, bottom: 64 },
      xAxis: {
        type: 'value',
        name: 'пикет',
        nameLocation: 'end',
        axisLabel: { formatter: (v: number) => (v < 0 ? '' : `ПК${Math.round(v / 10)}`) },
        splitLine: { lineStyle: { type: 'dashed', opacity: 0.4 } },
      },
      yAxis: { type: 'value', show: false, scale: true },
      dataZoom: [
        { type: 'inside', xAxisIndex: 0, filterMode: 'none' },
        { type: 'inside', yAxisIndex: 0, filterMode: 'none' },
        { type: 'slider', xAxisIndex: 0, filterMode: 'none', height: 18, bottom: 12, labelFormatter: (v: number) => `ПК${Math.round(v / 10)}` },
      ],
      tooltip: {
        trigger: 'item',
        confine: true,
        formatter: (p: { data?: { props?: ChannelProps } }) => {
          const c = p.data?.props;
          if (!c) return '';
          const lvl = c.risk_level;
          const esc = (s: string) => s.replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[ch]!);
          return [
            `<b>${esc(c.name)}</b> · № ${c.channel_id}`,
            `${esc(c.sensor_type)} · ${esc(c.system_type)}`,
            `Объект: ${esc(objectName.get(c.object_id) ?? '—')}`,
            lvl ? `Риск: <b style="color:${RISK_META[lvl].color}">${RISK_META[lvl].label}</b>, вероятность отказа ${Math.round((c.prob ?? 0) * 100)} %` : 'Прогноза нет',
            `Положение: ${c.picket ? esc(c.picket) + ', ' : ''}${PLACED[c.placed_by]}${c.side ? (c.side === 'left' ? ', лев.' : ', прав.') : ''}`,
            '<span style="opacity:.7">Клик — карточка датчика</span>',
          ].join('<br/>');
        },
      },
      series,
    };
  }, [collectors, channels, zones, levels, showZones, objectName, dark]);

  const onClick = useCallback(
    (e: echarts.ECElementEvent) => {
      const props = (e.data as { props?: ChannelProps } | undefined)?.props;
      if (props) nav(`/channels/${props.channel_id}`);
    },
    [nav],
  );

  const zoomTo = (f: Feature<CollectorProps>) => {
    const [[x0, y], [x1]] = f.geometry.coordinates as number[][];
    const pad = Math.max(200, (x1 - x0) * 2);
    chartRef.current?.dispatchAction({ type: 'dataZoom', dataZoomIndex: 0, startValue: x0 - pad, endValue: x1 + pad });
    chartRef.current?.dispatchAction({ type: 'dataZoom', dataZoomIndex: 1, startValue: y - 400, endValue: y + 400 });
  };
  const resetZoom = () => {
    chartRef.current?.dispatchAction({ type: 'dataZoom', dataZoomIndex: 0, start: 0, end: 100 });
    chartRef.current?.dispatchAction({ type: 'dataZoom', dataZoomIndex: 1, start: 0, end: 100 });
  };

  const counts = useMemo(() => {
    const c: Record<RiskLevel, number> = { normal: 0, attention: 0, risk: 0, critical: 0 };
    for (const f of channels) c[f.properties.risk_level ?? 'normal'] += 1;
    return c;
  }, [channels]);

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type="warning"
        showIcon
        message="Схема условная: геометрия синтетическая"
        description={
          <>
            Координат у заказчика нет. Оси коллекторов, боковые галереи и точки датчиков построены по пикетам из названий
            (1 ПК = 10 м) — это линейная схема, а не карта. Датчики без пикета — в узле своего объекта слева от ПК0.
            {cols.data?.stats && (
              <>
                {' '}
                Пикет разобран у {fmtNum((cols.data.stats as Record<string, number>).with_picket)} из{' '}
                {fmtNum((cols.data.stats as Record<string, number>).channels)} датчиков.
              </>
            )}
          </>
        }
      />
      <Row gutter={[16, 16]}>
        <Col xs={24} xxl={18}>
          <Card
            size="small"
            title={
              <Space wrap size={[12, 4]}>
                <span>Схема коллекторов по пикетам</span>
                <Checkbox.Group
                  value={levels}
                  onChange={(v) => setLevels(v as RiskLevel[])}
                  options={RISK_LEVELS.map((l) => ({
                    value: l,
                    label: (
                      <span style={{ color: RISK_META[l].color }}>
                        {RISK_META[l].label} ({fmtNum(counts[l])})
                      </span>
                    ),
                  }))}
                />
                <Checkbox checked={showZones} onChange={(e) => setShowZones(e.target.checked)}>
                  Проблемные участки
                </Checkbox>
              </Space>
            }
            extra={
              <Space>
                <Typography.Text type="secondary">Срез: {fmtDateTime(cols.data?.snapshot_at)}</Typography.Text>
                <Tooltip title="Показать всю схему">
                  <Button size="small" icon={<ExpandOutlined />} onClick={resetZoom} aria-label="Показать всю схему" />
                </Tooltip>
              </Space>
            }
          >
            <QueryState query={pts} skeletonHeight={620} isEmpty={(d) => !d.total} emptyText="Нет датчиков под фильтры">
              {() => (
                <>
                  <Chart
                    option={option}
                    height={620}
                    onClick={onClick}
                    instanceRef={chartRef}
                    ariaLabel="Схема коллекторов по пикетам: датчики цветом и формой по уровню риска"
                  />
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    Колесо мыши — масштаб (по горизонтали и вертикали), перетаскивание — сдвиг, ползунок внизу — участок
                    пикетов. Форма точки дублирует цвет: ● норма, ▲ внимание, ■ риск, ◆ критично.
                  </Typography.Text>
                </>
              )}
            </QueryState>
          </Card>
        </Col>
        <Col xs={24} xxl={6}>
          <Card size="small" title={`Проблемные участки (${zones.length})`}>
            {zones.length ? (
              <List
                size="small"
                dataSource={zones.slice(0, 40)}
                style={{ maxHeight: 600, overflow: 'auto' }}
                renderItem={(f) => (
                  <List.Item
                    actions={[
                      <Tooltip key="z" title="Приблизить участок на схеме">
                        <Button size="small" type="text" icon={<AimOutlined />} onClick={() => zoomTo(f)} aria-label="Приблизить" />
                      </Tooltip>,
                    ]}
                  >
                    <Space direction="vertical" size={0}>
                      <Typography.Text strong>{f.properties.name}</Typography.Text>
                      <Space size={4} wrap>
                        {(f.properties.critical ?? 0) > 0 && <Tag color="red">◆ {f.properties.critical} крит.</Tag>}
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {f.properties.channels} тревожных подряд, до {Math.round((f.properties.max_prob ?? 0) * 100)} %
                        </Typography.Text>
                      </Space>
                    </Space>
                  </List.Item>
                )}
              />
            ) : (
              <Empty description="Скоплений тревожных датчиков нет" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
              Участок — два и больше датчиков «Риск»/«Критично» на оси подряд, не дальше 5 пикетов (50 м) друг от друга.
            </Typography.Paragraph>
          </Card>
        </Col>
      </Row>
    </Space>
  );
}
