import { HistoryOutlined } from '@ant-design/icons';
import { Button, DatePicker, Space, Tag, Tooltip } from 'antd';
import dayjs from 'dayjs';
import { useAt, useDataMomentDefaults, useUrlAt } from '@/hooks/useAt';
import { fmtDateTime, toApi } from '@/utils/time';

/** Выбор даты и часа для «машины времени» + кнопка «Сейчас». Явно выбранный момент — в URL (?at=). */
export function TimeMachine() {
  const [at, setAt] = useAt();
  const urlAt = useUrlAt();
  const { defaultAt, lastSnapshot, sim } = useDataMomentDefaults();
  return (
    <Space size={8} wrap={false}>
      {urlAt ? (
        <Tag color="purple" icon={<HistoryOutlined />} style={{ marginInlineEnd: 0 }}>
          Машина времени
        </Tag>
      ) : (
        defaultAt && (
          <Tooltip
            title={
              <>
                Данные заказчика заканчиваются {fmtDateTime(lastSnapshot)}. «Сейчас» — последний момент, для которого уже известно,
                что случилось в следующие 24 ч: исход прогноза («Сбылся» / «Не сбылся») показан для проверки модели.
                <br />
                Любой другой момент — в календаре рядом.
              </>
            }
          >
            <Tag color="blue" style={{ marginInlineEnd: 0 }}>
              Последние данные
            </Tag>
          </Tooltip>
        )
      )}
      <DatePicker
        aria-label="Момент времени"
        showTime={{ format: 'HH', showMinute: false, showSecond: false }}
        format="DD.MM.YYYY HH:00"
        placeholder={sim ? 'Модельное время' : 'Текущий момент'}
        value={at ? dayjs(at) : null}
        allowClear={false}
        onChange={(d) => setAt(d ? toApi(d.startOf('hour')) : undefined)}
        style={{ width: 170 }}
        disabledDate={(d) => d.isAfter(dayjs(lastSnapshot ?? undefined).endOf('day'))}
      />
      <Tooltip
        title={
          sim
            ? 'Вернуться к модельному времени симуляции'
            : defaultAt
              ? `Вернуться к последним данным (${fmtDateTime(defaultAt)})`
              : 'Вернуться к текущему моменту'
        }
      >
        <Button onClick={() => setAt(undefined)} disabled={!urlAt}>
          Сейчас
        </Button>
      </Tooltip>
    </Space>
  );
}
