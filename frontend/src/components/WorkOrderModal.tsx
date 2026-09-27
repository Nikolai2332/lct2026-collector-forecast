import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Col, DatePicker, Descriptions, Form, Input, Modal, Row, Select, Spin } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/api/endpoints';
import { invalidateAfterWorkOrder, useRecommendation, useRecommendations } from '@/api/queries';
import { useAt, useAtNavigate } from '@/hooks/useAt';
import type { ChannelRef, FactorHuman, MaintenanceAdvice, ObjectRef, RiskLevel, WorkOrderPriority } from '@/types';
import { humanFactors } from '@/utils/factors';
import { WO_PRIORITY_DUE_HOURS, WO_PRIORITY_LABELS } from '@/utils/labels';
import { toApi } from '@/utils/time';
import { RiskBadge } from './RiskBadge';

export interface WorkOrderSource {
  prediction_id?: number | null;
  channel: ChannelRef;
  object: ObjectRef;
  risk_level?: RiskLevel | null;
  factors?: string[];
  factors_human?: FactorHuman[];
}

interface Props {
  open: boolean;
  /** Откуда заявка: прогноз или датчик. Не задан — датчик выбирается в форме */
  source?: WorkOrderSource | null;
  onClose: () => void;
}

interface FormValues {
  priority: WorkOrderPriority;
  due_at: Dayjs;
  reason_id?: number;
  recommendation_id?: number;
  recommendation_text?: string;
  description?: string;
  assignee?: string;
}

const PRIORITY_BY_RISK: Record<RiskLevel, WorkOrderPriority> = {
  critical: 'critical',
  risk: 'high',
  attention: 'medium',
  normal: 'low',
};

const describe = (s: WorkOrderSource, advice?: MaintenanceAdvice) => {
  const reasons = humanFactors({ factors: s.factors ?? [], factors_human: s.factors_human }).map((f) => `— ${f.text}`);
  const rec = advice ? [`Рекомендация (${advice.title}): ${advice.reason}`, ...(advice.avoid ?? []).map((x) => `Не делать: ${x}`)] : [];
  return reasons.length || rec.length
    ? [`Черновик из прогноза отказа на 24 ч по датчику «${s.channel.name}».`, ...rec, ...(reasons.length ? ['Причины прогноза:', ...reasons] : [])].join('\n')
    : `Заявка по датчику «${s.channel.name}».`;
};

/** Черновик заявки с автозаполнением: объект, датчик, причина, рекомендация, срок, приоритет. */
export function WorkOrderModal({ open, source, onClose }: Props) {
  const [form] = Form.useForm<FormValues>();
  const [at] = useAt();
  const nav = useAtNavigate();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const [picked, setPicked] = useState<number | null>(null);
  const [search, setSearch] = useState('');
  const dueTouched = useRef(false);
  const base = useMemo(() => (at ? dayjs(at) : dayjs().startOf('minute')), [at]);

  // Выбор датчика, если заявка создаётся со страницы «Заявки»
  const found = useQuery({
    queryKey: ['channels', 'search', at, search],
    queryFn: () => api.channels({ q: search || undefined, limit: 20, at }),
    enabled: open && !source,
  });
  const card = useQuery({
    queryKey: ['channel', picked, at],
    queryFn: () => api.channel(picked!, at),
    enabled: open && !source && picked != null,
  });
  const effective: WorkOrderSource | null = useMemo(() => {
    if (source) return source;
    if (!card.data) return null;
    const c = card.data;
    return {
      prediction_id: c.prediction?.prediction_id ?? null,
      channel: { id: c.id, name: c.name, sensor_type: c.sensor_type, system_type: c.system_type },
      object: c.object,
      risk_level: c.prediction?.risk_level ?? null,
      factors: c.prediction?.factors,
      factors_human: c.prediction?.factors_human,
    };
  }, [source, card.data]);

  const recs = useRecommendations(effective?.channel.sensor_type);
  // Рекомендация по ТО (правила): из неё заполняются действие, срок, приоритет, исполнитель и описание
  const advice = useRecommendation(open ? effective?.prediction_id : null);
  const a = advice.data && advice.data.prediction_id === effective?.prediction_id ? advice.data : undefined;
  const reasons = useQuery({ queryKey: ['reasons', 'dispatch'], queryFn: () => api.reasons('dispatch'), enabled: open });

  // Автозаполнение при смене источника и когда пришла рекомендация по ТО
  useEffect(() => {
    if (!open || !effective) return;
    const priority = a?.priority ?? (effective.risk_level ? PRIORITY_BY_RISK[effective.risk_level] : 'medium');
    // Срок из рекомендации не пересчитывается при смене приоритета — его задают правила
    dueTouched.current = !!a;
    form.setFieldsValue({
      priority,
      due_at: base.add(a ? a.due_hours : WO_PRIORITY_DUE_HOURS[priority], 'hour'),
      description: describe(effective, a),
      reason_id:
        effective.risk_level === 'critical' || effective.risk_level === 'risk'
          ? reasons.data?.find((r) => r.code === 'model_risk_confirmed')?.id
          : undefined,
      ...(a
        ? {
            recommendation_id: a.recommendation_id ?? undefined,
            recommendation_text: a.action,
            assignee: a.assignee !== '—' ? a.assignee : undefined,
          }
        : {}),
    });
  }, [open, effective, base, form, reasons.data, a]);

  useEffect(() => {
    // Заявка по датчику без прогноза — первая рекомендация из справочника для типа датчика
    if (open && recs.data?.length && !form.getFieldValue('recommendation_id') && !effective?.prediction_id) {
      form.setFieldsValue({ recommendation_id: recs.data[0].id, recommendation_text: recs.data[0].text });
    }
  }, [open, recs.data, form, effective?.prediction_id]);

  const reset = () => {
    form.resetFields();
    setPicked(null);
    setSearch('');
  };

  const save = useMutation({
    mutationFn: (v: FormValues) =>
      api.createWorkOrder(
        {
          prediction_id: effective?.prediction_id ?? null,
          channel_id: effective?.prediction_id ? null : effective!.channel.id,
          priority: v.priority,
          due_at: toApi(v.due_at),
          reason_id: v.reason_id ?? null,
          recommendation_id: v.recommendation_id ?? null,
          recommendation_text: v.recommendation_text || null,
          description: v.description || null,
          assignee: v.assignee || null,
        },
        at,
      ),
    onSuccess: (wo) => {
      invalidateAfterWorkOrder(qc);
      message.success({
        content: (
          <span>
            Черновик заявки {wo.number} сохранён.{' '}
            <Button type="link" size="small" onClick={() => nav(`/work-orders/${wo.id}`)} style={{ padding: 0 }}>
              Открыть
            </Button>
          </span>
        ),
        duration: 6,
      });
      reset();
      onClose();
    },
  });

  return (
    <Modal
      open={open}
      title="Черновик заявки на обслуживание"
      okText="Сохранить черновик"
      cancelText="Отмена"
      onOk={() => form.submit()}
      okButtonProps={{ disabled: !effective }}
      onCancel={() => {
        reset();
        onClose();
      }}
      confirmLoading={save.isPending}
      width={760}
      destroyOnHidden
    >
      {!source && (
        <Form.Item label="Датчик" required style={{ marginBottom: 12 }}>
          <Select
            showSearch
            placeholder="Начните вводить название, тег или номер датчика"
            filterOption={false}
            onSearch={setSearch}
            value={picked ?? undefined}
            onChange={setPicked}
            loading={found.isFetching}
            notFoundContent={found.isFetching ? <Spin size="small" /> : 'Ничего не найдено'}
            options={found.data?.items.map((c) => ({
              value: c.id,
              label: `${c.name} · ${c.object.name} · ${c.tag}`,
            }))}
          />
        </Form.Item>
      )}
      {card.isFetching && <Spin />}
      {effective && (
        <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
          <Descriptions.Item label="Объект" span={2}>
            {effective.object.path.join(' → ')}
          </Descriptions.Item>
          <Descriptions.Item label="Датчик">{effective.channel.name}</Descriptions.Item>
          <Descriptions.Item label="Риск">
            <RiskBadge level={effective.risk_level} />
          </Descriptions.Item>
          <Descriptions.Item label="Тип">{effective.channel.sensor_type}</Descriptions.Item>
          <Descriptions.Item label="Система">{effective.channel.system_type}</Descriptions.Item>
        </Descriptions>
      )}
      {a && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={`Заполнено по рекомендации: ${a.title}`}
          description="Приоритет, срок, исполнитель, действие и описание — из правил ТО (проект правил). Любое поле можно изменить."
        />
      )}
      {save.isError && <Alert type="error" showIcon message={(save.error as Error).message} style={{ marginBottom: 12 }} />}
      <Form
        form={form}
        layout="vertical"
        onFinish={(v) => save.mutate(v)}
        disabled={!effective}
        onValuesChange={(changed: Partial<FormValues>) => {
          if ('due_at' in changed) dueTouched.current = true;
          if (changed.priority && !dueTouched.current)
            form.setFieldValue('due_at', base.add(WO_PRIORITY_DUE_HOURS[changed.priority], 'hour'));
          if (changed.recommendation_id)
            form.setFieldValue('recommendation_text', recs.data?.find((r) => r.id === changed.recommendation_id)?.text);
        }}
      >
        <Row gutter={16}>
          <Col span={8}>
            <Form.Item name="priority" label="Приоритет" rules={[{ required: true }]}>
              <Select options={Object.entries(WO_PRIORITY_LABELS).map(([value, label]) => ({ value, label }))} />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="due_at" label="Срок" rules={[{ required: true, message: 'Укажите срок' }]}>
              <DatePicker showTime={{ format: 'HH:mm' }} format="DD.MM.YYYY HH:mm" style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="assignee" label="Исполнитель">
              <Input placeholder="Бригада, ФИО" />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="reason_id" label="Причина">
          <Select allowClear options={reasons.data?.map((r) => ({ value: r.id, label: r.name }))} placeholder="Из справочника" />
        </Form.Item>
        <Form.Item name="recommendation_id" label="Рекомендация">
          <Select
            loading={recs.isFetching}
            options={recs.data?.map((r) => ({ value: r.id, label: r.text }))}
            placeholder="Из справочника"
            optionRender={(o) => <span style={{ whiteSpace: 'normal' }}>{o.label}</span>}
          />
        </Form.Item>
        <Form.Item name="recommendation_text" label="Что сделать (можно уточнить)">
          <Input.TextArea rows={2} maxLength={2000} />
        </Form.Item>
        <Form.Item name="description" label="Описание">
          <Input.TextArea rows={4} maxLength={4000} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
