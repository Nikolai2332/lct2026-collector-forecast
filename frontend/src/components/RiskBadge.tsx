import {
  CheckCircleFilled,
  CloseCircleFilled,
  ExclamationCircleFilled,
  QuestionCircleOutlined,
  WarningFilled,
} from '@ant-design/icons';
import { Tag, Tooltip } from 'antd';
import type { ReactNode } from 'react';
import type { RiskLevel } from '@/types';
import { RISK_META } from '@/utils/risk';

const ICONS: Record<RiskLevel, ReactNode> = {
  normal: <CheckCircleFilled />,
  attention: <ExclamationCircleFilled />,
  risk: <WarningFilled />,
  critical: <CloseCircleFilled />,
};

interface Props {
  level?: RiskLevel | null;
  /** Дописать к подписи число, например вероятность */
  suffix?: ReactNode;
  /** Только иконка (подпись — во всплывающей подсказке и aria-label) */
  iconOnly?: boolean;
}

/** Уровень риска: цвет всегда дублируется иконкой и текстом (доступность). */
export function RiskBadge({ level, suffix, iconOnly }: Props) {
  if (!level && iconOnly)
    return (
      <Tooltip title="Нет прогноза">
        <span role="img" aria-label="Нет прогноза" style={{ color: '#bfbfbf' }}>
          <QuestionCircleOutlined />
        </span>
      </Tooltip>
    );
  if (!level)
    return (
      <Tag icon={<QuestionCircleOutlined />} color="default">
        Нет прогноза
      </Tag>
    );
  const m = RISK_META[level];
  if (iconOnly)
    return (
      <Tooltip title={m.label}>
        <span role="img" aria-label={m.label} style={{ color: m.color }}>
          {ICONS[level]}
        </span>
      </Tooltip>
    );
  return (
    <Tag
      icon={ICONS[level]}
      style={{ color: m.color, backgroundColor: m.bg, borderColor: m.color, fontWeight: 500, marginInlineEnd: 0 }}
      data-risk={level}
    >
      {m.label}
      {suffix != null && <> · {suffix}</>}
    </Tag>
  );
}
