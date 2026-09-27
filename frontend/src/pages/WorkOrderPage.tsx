import { PrinterOutlined, SaveOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Col, DatePicker, Descriptions, Form, Input, Row, Select, Space, Tag, Typography } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '@/api/endpoints';
import { invalidateAfterWorkOrder, useRecommendations } from '@/api/queries';
import { AtLink, ChannelLink } from '@/components/cells';
import { QueryState } from '@/components/QueryState';
import { StatusActions } from '@/components/StatusActions';
import { useAt } from '@/hooks/useAt';
import { useAuth } from '@/hooks/useAuth';
import type { WorkOrderOut, WorkOrderPriority } from '@/types';
import { WO_OPEN_STATUSES, WO_PRIORITY_COLORS, WO_PRIORITY_LABELS, WO_STATUS_COLORS } from '@/utils/labels';
import { fmtDateTime, toApi } from '@/utils/time';

interface FormValues {
  priority: WorkOrderPriority;
  due_at?: Dayjs | null;
  reason_id?: number | null;
  recommendation_id?: number | null;
  recommendation_text?: string | null;
  description?: string | null;
  assignee?: string | null;
}

function EditForm({ wo }: { wo: WorkOrderOut }) {
  const [form] = Form.useForm<FormValues>();
  const [at] = useAt();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const recs = useRecommendations(wo.channel.sensor_type);
  const reasons = useQuery({ queryKey: ['reasons', 'dispatch'], queryFn: () => api.reasons('dispatch') });

  useEffect(() => {
    form.setFieldsValue({
      priority: wo.priority,
      due_at: wo.due_at ? dayjs(wo.due_at) : null,
      reason_id: wo.reason?.id ?? null,
      recommendation_id: wo.recommendation?.id ?? null,
      recommendation_text: wo.recommendation_text,
      description: wo.description,
      assignee: wo.assignee,
    });
  }, [wo, form]);

  const save = useMutation({
    mutationFn: (v: FormValues) =>
      api.patchWorkOrder(
        wo.id,
        {
          priority: v.priority,
          due_at: v.due_at ? toApi(v.due_at) : null,
          reason_id: v.reason_id ?? null,
          recommendation_id: v.recommendation_id ?? null,
          recommendation_text: v.recommendation_text || null,
          description: v.description || null,
          assignee: v.assignee || null,
        },
        at,
      ),
    onSuccess: () => {
      invalidateAfterWorkOrder(qc);
      message.success('Заявка сохранена');
    },
  });

  return (
    <Card size="small" title="Правка заявки" className="no-print">
      {save.isError && <Alert type="error" showIcon message={(save.error as Error).message} style={{ marginBottom: 12 }} />}
      <Form
        form={form}
        layout="vertical"
        onFinish={(v) => save.mutate(v)}
        onValuesChange={(c: Partial<FormValues>) => {
          if (c.recommendation_id) form.setFieldValue('recommendation_text', recs.data?.find((r) => r.id === c.recommendation_id)?.text);
        }}
      >
        <Row gutter={16}>
          <Col xs={24} md={8}>
            <Form.Item name="priority" label="Приоритет" rules={[{ required: true }]}>
              <Select options={Object.entries(WO_PRIORITY_LABELS).map(([value, label]) => ({ value, label }))} />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item name="due_at" label="Срок">
              <DatePicker showTime={{ format: 'HH:mm' }} format="DD.MM.YYYY HH:mm" style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item name="assignee" label="Исполнитель">
              <Input />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="reason_id" label="Причина">
          <Select allowClear options={reasons.data?.map((r) => ({ value: r.id, label: r.name }))} />
        </Form.Item>
        <Form.Item name="recommendation_id" label="Рекомендация">
          <Select
            allowClear
            options={recs.data?.map((r) => ({ value: r.id, label: r.text }))}
            optionRender={(o) => <span style={{ whiteSpace: 'normal' }}>{o.label}</span>}
          />
        </Form.Item>
        <Form.Item name="recommendation_text" label="Что сделать">
          <Input.TextArea rows={2} />
        </Form.Item>
        <Form.Item name="description" label="Описание">
          <Input.TextArea rows={5} />
        </Form.Item>
        <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={save.isPending}>
          Сохранить
        </Button>
      </Form>
    </Card>
  );
}

export default function WorkOrderPage() {
  const id = Number(useParams().id);
  const [at] = useAt();
  const { canAct } = useAuth();
  const q = useQuery({ queryKey: ['workOrder', id, at], queryFn: () => api.workOrder(id, at) });
  return (
    <QueryState query={q} rows={10}>
      {(wo) => {
        const open = WO_OPEN_STATUSES.includes(wo.status);
        return (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <div className="page-title no-print">
              <Space wrap>
                <AtLink to="/work-orders">← Заявки</AtLink>
                <Typography.Title level={3}>Заявка {wo.number}</Typography.Title>
                <Tag color={WO_STATUS_COLORS[wo.status]}>{wo.status_label}</Tag>
                <Tag color={WO_PRIORITY_COLORS[wo.priority]}>Приоритет: {wo.priority_label}</Tag>
              </Space>
              <Space wrap>
                <StatusActions wo={wo} size="middle" />
                <Button icon={<PrinterOutlined />} onClick={() => window.print()}>
                  Распечатать
                </Button>
              </Space>
            </div>
            <Row gutter={[16, 16]} className="print-area-row">
              <Col xs={24} xxl={canAct && open ? 13 : 24}>
                <Card className="print-area">
                  <div className="print-only" style={{ marginBottom: 12 }}>
                    <Typography.Title level={3} style={{ margin: 0 }}>
                      Заявка на обслуживание № {wo.number}
                    </Typography.Title>
                    <Typography.Text>Служба эксплуатации коллекторов · сервис прогноза отказов оборудования</Typography.Text>
                  </div>
                  <Descriptions bordered size="small" column={1} styles={{ label: { width: 200 } }}>
                    <Descriptions.Item label="Статус">{wo.status_label}</Descriptions.Item>
                    <Descriptions.Item label="Приоритет">{wo.priority_label}</Descriptions.Item>
                    <Descriptions.Item label="Срок выполнения">{fmtDateTime(wo.due_at)}</Descriptions.Item>
                    <Descriptions.Item label="Объект">{wo.object.path.join(' → ')}</Descriptions.Item>
                    <Descriptions.Item label="Датчик">
                      <ChannelLink id={wo.channel.id} name={wo.channel.name} /> · {wo.channel.sensor_type} · {wo.channel.system_type} · № {wo.channel.id}
                    </Descriptions.Item>
                    <Descriptions.Item label="Причина">{wo.reason?.name ?? '—'}</Descriptions.Item>
                    <Descriptions.Item label="Рекомендация">{wo.recommendation_text ?? wo.recommendation?.text ?? '—'}</Descriptions.Item>
                    <Descriptions.Item label="Описание">
                      <div style={{ whiteSpace: 'pre-line' }}>{wo.description ?? '—'}</div>
                    </Descriptions.Item>
                    <Descriptions.Item label="Исполнитель">{wo.assignee ?? '—'}</Descriptions.Item>
                    <Descriptions.Item label="Создал">
                      {wo.created_by?.full_name ?? '—'}, {fmtDateTime(wo.created_at)}
                    </Descriptions.Item>
                    <Descriptions.Item label="Изменена">{fmtDateTime(wo.updated_at)}</Descriptions.Item>
                    {wo.closed_at && <Descriptions.Item label="Закрыта">{fmtDateTime(wo.closed_at)}</Descriptions.Item>}
                    {wo.prediction_id && <Descriptions.Item label="Прогноз">№ {wo.prediction_id}</Descriptions.Item>}
                  </Descriptions>
                  <div className="print-only" style={{ marginTop: 40 }}>
                    <Row gutter={32}>
                      <Col span={12}>Выдал: ____________________ / ____________</Col>
                      <Col span={12}>Принял: ____________________ / ____________</Col>
                    </Row>
                    <div style={{ marginTop: 24 }}>Отметка о выполнении: ________________________________________________</div>
                  </div>
                </Card>
              </Col>
              {canAct && open && (
                <Col xs={24} xxl={11}>
                  <EditForm wo={wo} />
                </Col>
              )}
            </Row>
          </Space>
        );
      }}
    </QueryState>
  );
}
