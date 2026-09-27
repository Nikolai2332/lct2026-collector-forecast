import type { RiskLevel } from '@/types';

export const RISK_LEVELS: RiskLevel[] = ['critical', 'risk', 'attention', 'normal'];

export const RISK_META: Record<RiskLevel, { label: string; color: string; bg: string; order: number }> = {
  normal: { label: 'Норма', color: '#389e0d', bg: '#f6ffed', order: 0 },
  attention: { label: 'Внимание', color: '#d4b106', bg: '#feffe6', order: 1 },
  risk: { label: 'Риск', color: '#d46b08', bg: '#fff7e6', order: 2 },
  critical: { label: 'Критично', color: '#cf1322', bg: '#fff1f0', order: 3 },
};

/** Цвета риска для светлой и тёмной темы. На тёмном фоне — светлее и насыщеннее, чтобы контраст текста и точек
 * оставался не ниже 4,5:1; уровень по-прежнему дублируется формой и подписью. */
const PALETTE: Record<'light' | 'dark', Record<RiskLevel, { color: string; bg: string }>> = {
  light: {
    normal: { color: '#389e0d', bg: '#f6ffed' },
    attention: { color: '#d4b106', bg: '#feffe6' },
    risk: { color: '#d46b08', bg: '#fff7e6' },
    critical: { color: '#cf1322', bg: '#fff1f0' },
  },
  dark: {
    normal: { color: '#73d13d', bg: '#162312' },
    attention: { color: '#fadb14', bg: '#2b2611' },
    risk: { color: '#ffa940', bg: '#2b1d11' },
    critical: { color: '#ff7875', bg: '#2a1215' },
  },
};

/** Меняет RISK_META на месте — все экраны берут цвета отсюда (вызывается при смене темы до перерисовки) */
export function applyRiskPalette(mode: 'light' | 'dark') {
  for (const level of RISK_LEVELS) Object.assign(RISK_META[level], PALETTE[mode][level]);
}

export const riskOf = (health: number): RiskLevel =>
  health >= 80 ? 'normal' : health >= 50 ? 'attention' : health >= 20 ? 'risk' : 'critical';

export const maxRisk = (a?: RiskLevel | null, b?: RiskLevel | null): RiskLevel | null => {
  if (!a) return b ?? null;
  if (!b) return a;
  return RISK_META[a].order >= RISK_META[b].order ? a : b;
};
