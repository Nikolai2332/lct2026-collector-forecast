import { type QueryClient, useQuery } from '@tanstack/react-query';
import { useAt } from '@/hooks/useAt';
import { api } from './endpoints';

/** Статус сервиса без входа: флаг demo включает быстрый выбор роли на экране входа */
export function useHealth() {
  return useQuery({ queryKey: ['health'], queryFn: api.health, staleTime: 5 * 60_000, retry: 1 });
}

/** Справочники меняются редко — кэшируем надолго */
export function useDictionaries() {
  return useQuery({ queryKey: ['dictionaries'], queryFn: api.dictionaries, staleTime: 60 * 60_000 });
}

export function useReasons(decisionType?: string) {
  return useQuery({
    queryKey: ['reasons', decisionType],
    queryFn: () => api.reasons(decisionType),
    enabled: !!decisionType,
    staleTime: 60 * 60_000,
  });
}

export function useRecommendations(sensorType?: string) {
  return useQuery({
    queryKey: ['recommendations', sensorType],
    queryFn: () => api.recommendations(sensorType),
    enabled: !!sensorType,
    staleTime: 60 * 60_000,
  });
}

/** Рекомендация по ТО для прогноза; факты берутся на срез прогноза, поэтому от `at` не зависит */
export function useRecommendation(predictionId?: number | null) {
  return useQuery({
    queryKey: ['recommendation', predictionId],
    queryFn: () => api.recommendation(predictionId!),
    enabled: predictionId != null,
    staleTime: 60_000,
  });
}

export function useThresholds() {
  const [at] = useAt();
  return useQuery({ queryKey: ['thresholds', at], queryFn: () => api.thresholds(at), staleTime: 10 * 60_000 });
}

/** После решения диспетчера: оно должно сразу появиться в журнале, на карточке, в топе и в ленте */
export function invalidateAfterDecision(qc: QueryClient) {
  for (const key of ['journal', 'predictions', 'prediction', 'channel', 'summary', 'notifications', 'objectDetail'])
    qc.invalidateQueries({ queryKey: [key] });
}

/** После создания или правки заявки */
export function invalidateAfterWorkOrder(qc: QueryClient) {
  for (const key of ['workOrders', 'workOrder', 'journal', 'predictions', 'prediction', 'channel', 'summary', 'recommendation', 'maintenancePlan'])
    qc.invalidateQueries({ queryKey: [key] });
}
