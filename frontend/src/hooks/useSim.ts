import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/endpoints';
import { useAuth } from './useAuth';

/**
 * Состояние симуляции потока. Опрос раз в 3 с, пока симуляция идёт (модельные часы на экране и обновление данных
 * при смене среза), и раз в 15 с в обычном режиме — чтобы заметить симуляцию, запущенную с другого рабочего места
 * или скриптом. Старый сервер без /api/sim — просто «выключено».
 */
export function useSimState() {
  const { user } = useAuth();
  return useQuery({
    queryKey: ['sim-state'],
    queryFn: api.simState,
    enabled: !!user,
    staleTime: 2_000,
    retry: false,
    refetchInterval: (q) => (q.state.data?.active ? 3_000 : 15_000),
  });
}

