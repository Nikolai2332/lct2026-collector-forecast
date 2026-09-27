import { InfoCircleOutlined } from '@ant-design/icons';
import { Tooltip, Typography } from 'antd';
import type { FactorHuman } from '@/types';
import { type FactorSource, humanFactors } from '@/utils/factors';

function TechLines({ items }: { items: FactorHuman[] }) {
  const tech = items.flatMap((f) => f.tech);
  if (!tech.length) return null;
  return (
    <div style={{ marginTop: 6, opacity: 0.75, fontSize: 12 }}>
      Подробнее для аналитика (признаки модели):
      {tech.map((t) => (
        <div key={t}>· {t}</div>
      ))}
    </div>
  );
}

/** «Главная причина» в таблицах: короткая версия, полный текст всех причин и формулировки модели — в подсказке */
export function MainFactor({ p }: { p: FactorSource }) {
  const items = humanFactors(p);
  if (!items.length) return <Typography.Text type="secondary">—</Typography.Text>;
  return (
    <Tooltip
      overlayStyle={{ maxWidth: 440 }}
      title={
        <>
          {items.map((f, i) => (
            <div key={f.text}>
              {i + 1}. {f.text}
            </div>
          ))}
          <TechLines items={items} />
        </>
      }
    >
      <span className="main-factor">{items[0].short}</span>
    </Tooltip>
  );
}

/** Три причины на карточке датчика; у каждой — значок с исходной формулировкой модели */
export function FactorList({ p }: { p: FactorSource }) {
  const items = humanFactors(p).slice(0, 3);
  return (
    <ol className="factor-list">
      {items.map((f) => (
        <li key={f.text}>
          {f.text}
          {f.tech.length > 0 && (
            <Tooltip
              overlayStyle={{ maxWidth: 440 }}
              title={
                <>
                  Подробнее для аналитика (признаки модели):
                  {f.tech.map((t) => (
                    <div key={t}>· {t}</div>
                  ))}
                  <div style={{ marginTop: 6, opacity: 0.75 }}>«обычно» — типичное значение у датчиков этого типа</div>
                </>
              }
            >
              <InfoCircleOutlined style={{ marginLeft: 6, color: 'var(--app-muted)' }} aria-label="Формулировка модели" />
            </Tooltip>
          )}
        </li>
      ))}
    </ol>
  );
}
