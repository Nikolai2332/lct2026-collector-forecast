import { useMutation, useQueryClient } from '@tanstack/react-query';
import { App, Alert, Form, Input, Modal, Radio, Select, Space, Typography } from 'antd';
import { useEffect } from 'react';
import { api } from '@/api/endpoints';
import { invalidateAfterDecision, useReasons } from '@/api/queries';
import { useAt } from '@/hooks/useAt';
import type { DecisionType, RiskLevel } from '@/types';
import { DECISION_LABELS } from '@/utils/labels';
import { fmtPct } from '@/utils/time';
import { RiskBadge } from './RiskBadge';

export interface DecisionTarget {
  prediction_id: number;
  channelName: string;
  objectName?: string;
  prob: number;
  risk_level: RiskLevel;
}

interface Props {
  target: DecisionTarget | null;
  /** Предвыбранное решение: «Взять на контроль» = «Наблюдение» */
  initialType?: DecisionType;
  onClose: () => void;
}

interface FormValues {
  decision_type: DecisionType;
  reason_id: number;
  comment?: string;
}

const TYPES: DecisionType[] = ['dispatch', 'false_alarm', 'monitor'];

/** Решение диспетчера: «Выезд бригады», «Ложное срабатывание», «Наблюдение»; причина обязательна. */
export function DecisionModal({ target, initialType, onClose }: Props) {
  const [form] = Form.useForm<FormValues>();
  const [at] = useAt();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const type = Form.useWatch('decision_type', form);
  const reasons = useReasons(type);

  useEffect(() => {
    if (target) form.setFieldsValue({ decision_type: initialType, reason_id: undefined, comment: '' });
  }, [target, initialType, form]);

  const save = useMutation({
    mutationFn: (v: FormValues) =>
      api.decide(target!.prediction_id, { decision_type: v.decision_type, reason_id: v.reason_id, comment: v.comment || null }, at),
    onSuccess: (_, v) => {
      invalidateAfterDecision(qc);
      message.success(`Решение «${DECISION_LABELS[v.decision_type]}» сохранено и появится в журнале прогнозов`);
      onClose();
    },
  });

  return (
    <Modal
      open={!!target}
      title="Решение диспетчера"
      okText="Сохранить решение"
      cancelText="Отмена"
      onOk={() => form.submit()}
      onCancel={onClose}
      confirmLoading={save.isPending}
      destroyOnHidden
      width={560}
    >
      {target && (
        <Space direction="vertical" size={4} style={{ marginBottom: 16 }}>
          <Typography.Text strong>{target.channelName}</Typography.Text>
          <Space size={8} wrap>
            {target.objectName && <Typography.Text type="secondary">{target.objectName}</Typography.Text>}
            <RiskBadge level={target.risk_level} suffix={`вероятность ${fmtPct(target.prob)}`} />
          </Space>
        </Space>
      )}
      {save.isError && <Alert type="error" showIcon message={(save.error as Error).message} style={{ marginBottom: 12 }} />}
      <Form
        form={form}
        layout="vertical"
        onFinish={(v) => save.mutate(v)}
        requiredMark="optional"
        onValuesChange={(c: Partial<FormValues>) => c.decision_type && form.resetFields(['reason_id'])}
      >
        <Form.Item name="decision_type" label="Решение" rules={[{ required: true, message: 'Выберите решение' }]}>
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            options={TYPES.map((t) => ({ value: t, label: DECISION_LABELS[t] }))}
          />
        </Form.Item>
        <Form.Item name="reason_id" label="Причина (из справочника)" rules={[{ required: true, message: 'Причина обязательна' }]}>
          <Select
            placeholder={type ? 'Выберите причину' : 'Сначала выберите решение'}
            disabled={!type}
            loading={reasons.isFetching}
            options={reasons.data?.map((r) => ({ value: r.id, label: r.name }))}
            showSearch
            optionFilterProp="label"
          />
        </Form.Item>
        <Form.Item name="comment" label="Комментарий">
          <Input.TextArea rows={3} maxLength={1000} showCount placeholder="По желанию" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
