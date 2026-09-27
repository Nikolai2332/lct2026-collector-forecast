import { Typography } from 'antd';
import type { ObjectNode, RiskLevel } from '@/types';
import { byUrgency, critOf as crit, riskOf as risky } from '@/utils/objects';
import { nWord } from '@/utils/plural';
import { RISK_META } from '@/utils/risk';
import { RiskBadge } from './RiskBadge';

interface Props {
  nodes: ObjectNode[];
  selectedId?: number | null;
  onSelect?: (node: ObjectNode) => void;
  /** Минимальная ширина плитки */
  minWidth?: number;
  compact?: boolean;
  /** Сколько критичных считать «максимально ярко»; по умолчанию — максимум среди этих плиток. Одна шкала на экран */
  scaleMax?: number;
}

/** Уровень, по которому красим плитку: наличие хотя бы одного критичного не делает весь объект одинаково красным */
function tileLevel(n: ObjectNode): RiskLevel | null {
  if (crit(n) > 0) return 'critical';
  if (risky(n) > 0) return 'risk';
  if ((n.risk_counts.attention ?? 0) > 0) return 'attention';
  return n.max_risk_level ? 'normal' : null;
}

/**
 * Плитки объектов, упорядоченные по срочности. Заметность плитки растёт с числом критичных датчиков
 * (насыщенность фона и толщина полосы слева), число крупно — «N из M»; цвет продублирован иконкой и текстом.
 */
export function ObjectTiles({ nodes, selectedId, onSelect, minWidth = 150, compact, scaleMax }: Props) {
  const sorted = [...nodes].sort(byUrgency);
  const maxCrit = Math.max(1, scaleMax ?? 0, ...nodes.map(crit));
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(auto-fill, minmax(${minWidth}px, 1fr))`, gap: 8 }}>
      {sorted.map((n) => {
        const level = tileLevel(n);
        const m = level ? RISK_META[level] : null;
        const c = crit(n);
        const r = risky(n);
        // Доля от максимума среди плиток: 1–2 критичных — почти белая плитка, максимум — насыщенная
        const weight = c / maxCrit;
        const background = c > 0 ? `rgba(207, 19, 34, ${(0.03 + 0.22 * weight).toFixed(3)})` : 'var(--tile-bg)';
        const label = `${n.name}: критичных ${c} из ${n.channels_count}${r ? `, риск ${r}` : ''}`;
        return (
          <div
            key={n.id}
            role="button"
            tabIndex={0}
            aria-label={label}
            title={label}
            className={`tile${selectedId === n.id ? ' selected' : ''}`}
            style={{
              background,
              borderColor: 'var(--app-border)',
              borderLeftColor: m ? m.color : 'var(--tile-empty-border)',
              borderLeftWidth: c > 0 ? Math.round(3 + 5 * weight) : 3,
              opacity: level === 'normal' || !level ? 0.85 : 1,
            }}
            onClick={() => onSelect?.(n)}
            onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onSelect?.(n)}
            data-risk={level ?? 'none'}
            data-critical={c}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 4, alignItems: 'center' }}>
              <Typography.Text strong ellipsis style={{ fontSize: compact ? 12 : 14 }}>
                {n.name.replace(/^объект /, '')}
              </Typography.Text>
              <RiskBadge level={level} iconOnly />
            </div>
            {c > 0 ? (
              <div style={{ lineHeight: 1.2 }}>
                <span style={{ fontSize: compact ? 18 : 20, fontWeight: 700, color: RISK_META.critical.color }}>{c}</span>{' '}
                <span style={{ fontSize: 12, color: RISK_META.critical.color }}>крит.</span>
                <div style={{ fontSize: 12, color: 'var(--app-muted)' }}>
                  из {n.channels_count.toLocaleString('ru-RU')}
                  {r > 0 && <span style={{ color: RISK_META.risk.color }}> · риск {r}</span>}
                </div>
              </div>
            ) : (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                критичных нет · {nWord(n.channels_count, 'датчик', 'датчика', 'датчиков')}
              </Typography.Text>
            )}
            {c === 0 && r > 0 && <div style={{ fontSize: 12, color: RISK_META.risk.color }}>риск {r}</div>}
          </div>
        );
      })}
    </div>
  );
}
