import { useMutation, useQueryClient } from '@tanstack/react-query';
import { App, Button, Popconfirm, Space } from 'antd';
import { api } from '@/api/endpoints';
import { invalidateAfterWorkOrder, sendToWork, SendToWorkError } from '@/api/queries';
import { useAt } from '@/hooks/useAt';
import { useAuth } from '@/hooks/useAuth';
import type { WorkOrderOut, WorkOrderStatus } from '@/types';
import { WO_STATUS_LABELS, WO_TRANSITIONS } from '@/utils/labels';

const ACTION_LABELS: Record<WorkOrderStatus, string> = {
  draft: 'Вернуть в черновик',
  submitted: 'Отправить в работу',
  in_progress: 'Взять в работу',
  done: 'Выполнена',
  cancelled: 'Отменить',
};

/** Кнопки смены статуса — только разрешённые переходы (как на бэкенде).
 * У черновика «Отправить в работу» делает два перехода подряд: черновик → отправлена → в работе
 * (оба шага — одно право work_order_status, поэтому кнопка видна тем же ролям, что и раньше). */
export function StatusActions({ wo, size = 'small' }: { wo: WorkOrderOut; size?: 'small' | 'middle' }) {
  const { canChangeStatus } = useAuth();
  const [at] = useAt();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const mut = useMutation({
    mutationFn: (status: WorkOrderStatus) =>
      wo.status === 'draft' && status === 'submitted' ? sendToWork(wo.id, at) : api.patchWorkOrder(wo.id, { status }, at),
    onSuccess: (res) => {
      invalidateAfterWorkOrder(qc);
      message.success(`Заявка ${res.number}: статус «${WO_STATUS_LABELS[res.status]}»`);
    },
    onError: (e) => {
      // Первый шаг мог пройти — статус заявки изменился, обновляем экраны в любом случае
      if (e instanceof SendToWorkError) invalidateAfterWorkOrder(qc);
      message.error({ content: e instanceof Error ? e.message : 'Не удалось сменить статус', duration: 8 });
    },
  });
  if (!canChangeStatus) return null;
  const next = WO_TRANSITIONS[wo.status];
  if (!next.length) return null;
  return (
    <Space size={4} wrap>
      {next.map((s) =>
        s === 'cancelled' ? (
          <Popconfirm key={s} title="Отменить заявку?" okText="Отменить заявку" cancelText="Нет" onConfirm={() => mut.mutate(s)}>
            <Button size={size} danger loading={mut.isPending && mut.variables === s}>
              {ACTION_LABELS[s]}
            </Button>
          </Popconfirm>
        ) : (
          <Button
            key={s}
            size={size}
            type={s === 'draft' ? 'default' : 'primary'}
            ghost={s !== 'draft'}
            loading={mut.isPending && mut.variables === s}
            onClick={() => mut.mutate(s)}
          >
            {ACTION_LABELS[s]}
          </Button>
        ),
      )}
    </Space>
  );
}
