import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Descriptions, Divider, Drawer, Form, InputNumber, Space, Typography } from 'antd';
import { useEffect } from 'react';
import { api } from '@/api/endpoints';
import type { SettingsIn } from '@/types';
import { RISK_META } from '@/utils/risk';
import { fmtDateTime } from '@/utils/time';

/** Границы в форме — в процентах, в API — доли */
interface FormValues {
  attention: number;
  risk: number;
  critical: number;
  notify_cooldown_hours: number;
  notify_per_slice: number;
  notify_sim_per_slice: number;
}

const toForm = (s: SettingsIn): FormValues => ({
  attention: Math.round(s.risk_attention * 100),
  risk: Math.round(s.risk_risk * 100),
  critical: Math.round(s.risk_critical * 100),
  notify_cooldown_hours: s.notify_cooldown_hours,
  notify_per_slice: s.notify_per_slice,
  notify_sim_per_slice: s.notify_sim_per_slice,
});
const toApi = (v: FormValues): SettingsIn => ({
  risk_attention: v.attention / 100,
  risk_risk: v.risk / 100,
  risk_critical: v.critical / 100,
  notify_cooldown_hours: v.notify_cooldown_hours,
  notify_per_slice: v.notify_per_slice,
  notify_sim_per_slice: v.notify_sim_per_slice,
});

/** «Настройки» администратора: границы уровней риска и лимиты уведомлений. Действуют сразу, без перезапуска. */
export function SettingsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [form] = Form.useForm<FormValues>();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const q = useQuery({ queryKey: ['settings'], queryFn: api.settings, enabled: open });
  useEffect(() => {
    if (open && q.data) form.setFieldsValue(toForm(q.data));
  }, [open, q.data, form]);
  const save = useMutation({
    mutationFn: (v: FormValues) => api.saveSettings(toApi(v)),
    onSuccess: () => {
      // Уровни риска меняются во всех экранах — перечитываем всё
      qc.invalidateQueries();
      message.success('Настройки сохранены и уже действуют');
      onClose();
    },
  });
  const v = Form.useWatch([], form) as FormValues | undefined;
  const ordered = v && v.attention < v.risk && v.risk < v.critical;

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="Настройки"
      width={Math.min(520, window.innerWidth - 16)}
      destroyOnHidden
      extra={
        <Space>
          <Button onClick={() => q.data && form.setFieldsValue(toForm(q.data.defaults))}>По умолчанию</Button>
          <Button type="primary" loading={save.isPending} disabled={!ordered} onClick={() => form.submit()}>
            Сохранить
          </Button>
        </Space>
      }
    >
      {save.isError && <Alert type="error" showIcon message={(save.error as Error).message} style={{ marginBottom: 12 }} />}
      <Form form={form} layout="vertical" onFinish={(x) => save.mutate(x)} requiredMark={false}>
        <Typography.Title level={5} style={{ marginTop: 0 }}>
          Границы уровней риска
        </Typography.Title>
        <Typography.Paragraph type="secondary">
          По вероятности отказа в ближайшие 24 ч. Уровень пересчитывается сразу во всех экранах, фильтрах и счётчиках;
          прогнозы в базе не меняются. По умолчанию — как у модели: 20 / 50 / 80 %.
        </Typography.Paragraph>
        <Space size={12} wrap>
          {(['attention', 'risk', 'critical'] as const).map((k) => (
            <Form.Item key={k} name={k} label={`«${RISK_META[k].label}» от, %`} rules={[{ required: true }]}>
              <InputNumber min={1} max={99} style={{ width: 130 }} />
            </Form.Item>
          ))}
        </Space>
        {v && (
          <Descriptions size="small" column={1} bordered style={{ marginBottom: 12 }}>
            <Descriptions.Item label={RISK_META.normal.label}>меньше {v.attention} %</Descriptions.Item>
            <Descriptions.Item label={RISK_META.attention.label}>{`${v.attention}–${v.risk} %`}</Descriptions.Item>
            <Descriptions.Item label={RISK_META.risk.label}>{`${v.risk}–${v.critical} %`}</Descriptions.Item>
            <Descriptions.Item label={RISK_META.critical.label}>{v.critical} % и выше</Descriptions.Item>
          </Descriptions>
        )}
        {v && !ordered && <Alert type="warning" showIcon message="Границы должны возрастать: «Внимание» < «Риск» < «Критично»" />}
        <Divider />
        <Typography.Title level={5}>Уведомления о переходах в «Критично»</Typography.Title>
        <Form.Item name="notify_cooldown_hours" label="Повторно по тому же датчику — не чаще, чем раз в (часов)">
          <InputNumber min={1} max={168} />
        </Form.Item>
        <Form.Item name="notify_per_slice" label="Лента и колокольчик: отдельных уведомлений на срез (остальные — сводкой)">
          <InputNumber min={1} max={20} />
        </Form.Item>
        <Form.Item name="notify_sim_per_slice" label="Симуляция: всплывающих уведомлений на срез (остальные — сводкой)">
          <InputNumber min={1} max={50} />
        </Form.Item>
      </Form>
      {q.data?.updated_at && (
        <Typography.Text type="secondary">
          Последнее изменение: {fmtDateTime(q.data.updated_at)}, {q.data.updated_by}. Изменения пишутся в журнал аудита.
        </Typography.Text>
      )}
    </Drawer>
  );
}
