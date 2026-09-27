import { useMutation, useQueryClient } from '@tanstack/react-query';
import { App, Button, Popconfirm, Space } from 'antd';
import { api } from '@/api/endpoints';
import { invalidateAfterWorkOrder } from '@/api/queries';
import { useAt } from '@/hooks/useAt';
import { useAuth } from '@/hooks/useAuth';
import type { WorkOrderOut, WorkOrderStatus } from '@/types';
import { WO_STATUS_LABELS, WO_TRANSITIONS } from '@/utils/labels';

const ACTION_LABELS: Record<WorkOrderStatus, string> = {
  draft: 'Вернуть в черновик',
  submitted: 'Отправить',
  in_progress: 'Взять в работу',
  done: 'Выполнена',
  cancelled: 'Отменить',
};

/** Кнопки смены статуса — только разрешённые переходы (как на бэкенде) */
export function StatusActions({ wo, size = 'small' }: { wo: WorkOrderOut; size?: 'small' | 'middle' }) {
  const { canChangeStatus } = useAuth();
  const [at] = useAt();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const mut = useMutation({
    mutationFn: (status: WorkOrderStatus) => api.patchWorkOrder(wo.id, { status }, at),
    onSuccess: (res) => {
      invalidateAfterWorkOrder(qc);
      message.success(`Заявка ${res.number}: статус «${WO_STATUS_LABELS[res.status]}»`);
    },
    onError: (e) => message.error(e instanceof Error ? e.message : 'Не удалось сменить статус'),
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
