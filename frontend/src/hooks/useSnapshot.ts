import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { api } from '@/api/endpoints';
import { useAt } from './useAt';

/**
 * Момент, от которого считать периоды по умолчанию. Если выбран «Сейчас», а последний срез прогнозов старше суток
 * (демо-данные за июль–август), берём момент среза — иначе графики и журнал будут пустыми.
 */
export function useDataMoment() {
  const [at] = useAt();
  const summary = useQuery({ queryKey: ['summary', at], queryFn: () => api.summary(at), enabled: !at });
  const snapshot = summary.data?.snapshot_at ?? null;
  const stale = !at && !!snapshot && dayjs().diff(dayjs(snapshot), 'hour') > 24;
  return {
    at,
    /** Для расчёта периодов по умолчанию */
    moment: dayjs(at ?? (stale ? snapshot : undefined)),
    stale,
    snapshot,
    ready: !!at || !summary.isPending,
  };
}
