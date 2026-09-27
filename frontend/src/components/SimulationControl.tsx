import { PauseCircleOutlined, PlayCircleOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, DatePicker, Descriptions, Form, Modal, Select, Tag, Tooltip, Typography } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { useState } from 'react';
import { api } from '@/api/endpoints';
import { useUrlAt } from '@/hooks/useAt';
import { useAuth } from '@/hooks/useAuth';
import { useSimState } from '@/hooks/useSim';
import type { SimStateOut } from '@/types';
import { fmtDate, fmtDateTime } from '@/utils/time';

const SPEEDS = [
  { value: 1, label: '×1 — реальное время' },
  { value: 10, label: '×10 — сутки за 2 ч 24 мин' },
  { value: 60, label: '×60 — сутки за 24 мин' },
  { value: 120, label: '×120 — сутки за 12 мин' },
  { value: 300, label: '×300 — сутки за 4 мин 48 с' },
  { value: 600, label: '×600 — сутки за 2 мин 24 с' },
];

const STOP_REASONS: Record<string, string> = {
  manual: 'остановлена вручную',
  finished: 'сутки проиграны до конца',
  restart: 'сброшена при перезапуске сервера',
};

/** Метка «Симуляция ×60 · 15.06.2026 14:32» (видна всем) и кнопка запуска/остановки (инженер и администратор) */
export function SimulationControl() {
  const { can } = useAuth();
  const { data: st } = useSimState();
  const urlAt = useUrlAt();
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const { message } = App.useApp();
  const canControl = can('sim_control');

  const stop = useMutation({
    mutationFn: api.simStop,
    onSuccess: (s) => {
      qc.setQueryData(['sim-state'], s);
      void qc.invalidateQueries({ queryKey: ['data-moment'] });
      message.info('Симуляция остановлена — экраны вернулись к обычному режиму');
    },
    onError: (e) => message.error(e.message),
  });

  if (!st?.enabled) return null;

  const hint = urlAt
    ? 'Сейчас выбрана «машина времени» — она важнее симуляции: экраны показывают выбранный момент. Нажмите «Сейчас», чтобы вернуться к модельному времени.'
    : 'Экраны показывают модельный момент и обновляются сами. Исходы, которые по модельному времени ещё не наступили, скрыты.';

  return (
    <>
      {st.active && (
        <Tooltip
          title={
            <>
              <div>{st.note}</div>
              <div style={{ marginTop: 6 }}>{hint}</div>
            </>
          }
        >
          <Tag
            color={urlAt ? 'default' : 'magenta'}
            icon={<PlayCircleOutlined />}
            style={{ marginInlineEnd: 0, cursor: canControl ? 'pointer' : 'default' }}
            onClick={canControl ? () => setOpen(true) : undefined}
            data-testid="sim-badge"
          >
            Симуляция ×{st.speed} · {fmtDateTime(st.model_time)}
          </Tag>
        </Tooltip>
      )}
      {canControl &&
        (st.active ? (
          <Tooltip title="Остановить симуляцию">
            <Button
              icon={<PauseCircleOutlined />}
              aria-label="Остановить симуляцию"
              loading={stop.isPending}
              onClick={() => stop.mutate()}
            />
          </Tooltip>
        ) : (
          <Button icon={<PlayCircleOutlined />} onClick={() => setOpen(true)}>
            Симуляция
          </Button>
        ))}
      {open && <SimulationModal state={st} onClose={() => setOpen(false)} />}
    </>
  );
}

function SimulationModal({ state, onClose }: { state: SimStateOut; onClose: () => void }) {
  const qc = useQueryClient();
  const { message } = App.useApp();
  const [day, setDay] = useState<Dayjs | null>(state.default_day ? dayjs(state.default_day) : null);
  const [speed, setSpeed] = useState<number>(60);

  const start = useMutation({
    mutationFn: () => api.simStart({ day: day!.format('YYYY-MM-DD'), speed }),
    onSuccess: (s) => {
      qc.setQueryData(['sim-state'], s);
      message.success(`Симуляция ${fmtDate(s.day)} запущена, ×${s.speed}`);
      onClose();
    },
    onError: (e) => message.error(e.message),
  });
  const stop = useMutation({
    mutationFn: api.simStop,
    onSuccess: (s) => {
      qc.setQueryData(['sim-state'], s);
      void qc.invalidateQueries({ queryKey: ['data-moment'] });
      onClose();
    },
    onError: (e) => message.error(e.message),
  });

  const from = state.available_from ? dayjs(state.available_from) : null;
  const to = state.available_to ? dayjs(state.available_to) : null;

  return (
    <Modal
      open
      title="Симуляция потока событий"
      onCancel={onClose}
      footer={
        state.active ? (
          <Button danger icon={<PauseCircleOutlined />} loading={stop.isPending} onClick={() => stop.mutate()}>
            Остановить
          </Button>
        ) : (
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            disabled={!day}
            loading={start.isPending}
            onClick={() => start.mutate()}
          >
            Запустить
          </Button>
        )
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="Воспроизведение заранее рассчитанных прогнозов"
        description="Модельные часы идут с ускорением; на каждом новом срезе появляются прогнозы этого часа, новые критические приходят уведомлениями. Онлайн-пересчёт моделью не выполняется. Данные в базе не меняются."
      />
      {state.active ? (
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="Сутки">{fmtDate(state.day)}</Descriptions.Item>
          <Descriptions.Item label="Модельное время">{fmtDateTime(state.model_time)}</Descriptions.Item>
          <Descriptions.Item label="Скорость">×{state.speed}</Descriptions.Item>
          <Descriptions.Item label="Запустил">{state.started_by ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="Событий получено">
            {state.events_received} (новых {state.events_accepted})
          </Descriptions.Item>
          <Descriptions.Item label="Критических уведомлений">{state.critical_sent}</Descriptions.Item>
        </Descriptions>
      ) : (
        <Form layout="vertical">
          <Form.Item
            label="Сутки"
            extra={from && to ? `Прогнозы есть с ${fmtDate(state.available_from)} по ${fmtDate(state.available_to)}` : 'В базе нет прогнозов'}
          >
            <DatePicker
              value={day}
              onChange={setDay}
              format="DD.MM.YYYY"
              allowClear={false}
              style={{ width: '100%' }}
              disabledDate={(d) => !from || !to || d.isBefore(from, 'day') || d.isAfter(to, 'day')}
            />
          </Form.Item>
          <Form.Item label="Скорость">
            <Select value={speed} onChange={setSpeed} options={SPEEDS} />
          </Form.Item>
          {state.stop_reason && state.day && (
            <Typography.Text type="secondary">
              Прошлая симуляция: {fmtDate(state.day)}, {STOP_REASONS[state.stop_reason] ?? state.stop_reason}.
            </Typography.Text>
          )}
          <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
            Поток событий суток в API приёма можно проиграть и скриптом:{' '}
            <Typography.Text code>python -m scripts.simulate_stream --day ГГГГ-ММ-ДД --speed 60</Typography.Text>
          </Typography.Paragraph>
        </Form>
      )}
    </Modal>
  );
}
