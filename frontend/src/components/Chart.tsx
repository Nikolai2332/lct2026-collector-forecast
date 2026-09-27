import { BarChart, LineChart, ScatterChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import langRU from 'echarts/lib/i18n/langRU.js';
import { CanvasRenderer } from 'echarts/renderers';
import { type CSSProperties, useEffect, useRef } from 'react';
import { useThemeMode } from '@/hooks/useThemeMode';

echarts.use([
  BarChart,
  LineChart,
  ScatterChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
  MarkPointComponent,
  MarkAreaComponent,
  CanvasRenderer,
]);
echarts.registerLocale('RU', langRU);

interface Props {
  option: echarts.EChartsCoreOption;
  height?: number;
  style?: CSSProperties;
  ariaLabel?: string;
  /** Клик по элементу графика (точка датчика на схеме) */
  onClick?: (params: echarts.ECElementEvent) => void;
  /** Доступ к экземпляру: масштабирование к участку схемы */
  instanceRef?: { current: echarts.ECharts | null };
}

/** ECharts напрямую: только нужные модули (меньше бандл), подгонка размера под контейнер */
export function Chart({ option, height = 320, style, ariaLabel, onClick, instanceRef }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const { mode } = useThemeMode();

  // Тема ECharts меняется только пересозданием экземпляра — поэтому mode в зависимостях
  useEffect(() => {
    const el = ref.current!;
    const inst = echarts.init(el, mode === 'dark' ? 'dark' : undefined, { renderer: 'canvas', locale: 'RU' });
    chart.current = inst;
    if (instanceRef) instanceRef.current = inst;
    const ro = new ResizeObserver(() => inst.resize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      inst.dispose();
      chart.current = null;
      if (instanceRef) instanceRef.current = null;
    };
  }, [instanceRef, mode]);

  useEffect(() => {
    const inst = chart.current;
    if (!inst || !onClick) return;
    inst.on('click', onClick);
    return () => {
      inst.off('click', onClick);
    };
  }, [onClick, mode]);

  useEffect(() => {
    // Фон — от карточки (своя тема ECharts рисует тёмно-синий)
    chart.current?.setOption({ backgroundColor: 'transparent', ...option }, { notMerge: true, lazyUpdate: true });
  }, [option, mode]);

  return <div ref={ref} role="img" aria-label={ariaLabel} style={{ height, width: '100%', ...style }} />;
}
