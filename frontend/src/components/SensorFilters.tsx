import { Select, Space } from 'antd';
import { useDictionaries } from '@/api/queries';

interface Props {
  system?: string;
  sensor?: string;
  onChange: (v: { system?: string; sensor?: string }) => void;
}

/** Фильтры «система» и «тип датчика» из справочника; типы сужаются по выбранной системе */
export function SensorFilters({ system, sensor, onChange }: Props) {
  const dict = useDictionaries();
  const sensors = (dict.data?.sensor_types ?? []).filter((s) => !system || s.system_type === system);
  return (
    <Space wrap>
      <Select
        allowClear
        placeholder="Система"
        style={{ width: 210 }}
        value={system}
        loading={dict.isPending}
        options={dict.data?.system_types.map((s) => ({ value: s, label: s }))}
        onChange={(v?: string) => onChange({ system: v, sensor: v && sensor && !sensors.some((s) => s.name === sensor && s.system_type === v) ? undefined : sensor })}
        aria-label="Система"
      />
      <Select
        allowClear
        showSearch
        placeholder="Тип датчика"
        style={{ width: 230 }}
        value={sensor}
        loading={dict.isPending}
        options={sensors.map((s) => ({ value: s.name, label: `${s.name} (${s.channels_count})` }))}
        onChange={(v?: string) => onChange({ system, sensor: v })}
        aria-label="Тип датчика"
      />
    </Space>
  );
}
