import { type Query, useQuery, useQueryClient } from '@tanstack/react-query';
import { Spin } from 'antd';
import dayjs from 'dayjs';
import { type ReactNode, useEffect, useRef } from 'react';
import { api } from '@/api/endpoints';
import { DataMomentCtx } from '@/hooks/dataMoment';
import { useSimState } from '@/hooks/useSim';
import { toApi } from '@/utils/time';

/** Горизонт прогноза: у срезов моложе него исход ещё неизвестен */
const HORIZON_H = 24;
/** Явный далёкий at: последний срез в базе, даже если идёт симуляция (без at сервер ответил бы на модельный момент) */
const DATA_END_PROBE = '2100-01-01T00:00:00';
/** Эти запросы от момента времени не зависят — при ходе модельных часов их не перезапрашиваем */
const STATIC_QUERIES = new Set(['sim-state', 'health', 'dictionaries', 'reasons', 'recommendations', 'thresholds', 'data-moment']);
const timeDependent = (q: Query) => !STATIC_QUERIES.has(String(q.queryKey[0]));

/**
 * Если данные в базе старше суток (реальный журнал заканчивается 30.06.2026, демо-набор — августом 2026),
 * режим «Сейчас» указывает не на текущее время (экраны были бы пустыми), а на последний момент с закрытым
 * горизонтом прогноза: последний срез − 24 ч. Для реальных данных это 29.06.2026 23:00.
 *
 * Во время симуляции потока «Сейчас» — модельное время: запросы уходят без at, момент подставляет сервер
 * (и скрывает ещё не наступившие по модельному времени исходы). Данные перезапрашиваются при каждом новом
 * модельном часе. Явно выбранная «машина времени» (?at=) важнее симуляции.
 */
export function DataMomentProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ['data-moment'],
    queryFn: () => api.objects({ at: DATA_END_PROBE }),
    staleTime: 10 * 60_000,
    select: (d) => d.snapshot_at ?? null,
  });
  const simQ = useSimState();
  const sim = simQ.data?.enabled && simQ.data.active ? simQ.data : undefined;

  // Запуск и остановка симуляции меняют «Сейчас» у всех экранов; ход часов — каждый модельный час
  const hourKey = sim ? (sim.model_time ?? '').slice(0, 13) : 'off';
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    void qc.invalidateQueries({ predicate: timeDependent });
  }, [hourKey, qc]);

  if (q.isPending || simQ.isPending) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin size="large" />
      </div>
    );
  }
  const snap = q.data;
  const stale = !!snap && dayjs().diff(dayjs(snap), 'hour') > HORIZON_H;
  const defaultAt = !sim && stale ? toApi(dayjs(snap).subtract(HORIZON_H, 'hour').startOf('hour')) : undefined;
  return <DataMomentCtx.Provider value={{ defaultAt, lastSnapshot: snap, sim }}>{children}</DataMomentCtx.Provider>;
}
