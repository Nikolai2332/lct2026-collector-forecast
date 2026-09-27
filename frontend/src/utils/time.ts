import dayjs, { type Dayjs } from 'dayjs';

/** Бэкенд работает в московском времени без пояса: `2026-08-01T18:42:10`. Храним и передаём строкой в этом виде. */
export const API_FORMAT = 'YYYY-MM-DDTHH:mm:ss';

export const toApi = (d: Dayjs): string => d.format(API_FORMAT);
/** Строку без пояса dayjs разбирает как локальное время — для отображения этого и нужно (показываем «как есть», МСК). */
export const fromApi = (s: string): Dayjs => dayjs(s);

/** Округление до десятых: Statistic из Ant Design с precision отбрасывает знаки, а не округляет (10,69 → 10,6). */
export const round1 = (v: number): number => Math.round(v * 10) / 10;

export const fmtDateTime = (s?: string | null): string => (s ? dayjs(s).format('DD.MM.YYYY HH:mm') : '—');
export const fmtDate = (s?: string | null): string => (s ? dayjs(s).format('DD.MM.YYYY') : '—');
export const fmtPct = (v?: number | null, digits = 0): string =>
  v == null ? '—' : `${(v * 100).toFixed(digits).replace('.', ',')} %`;
export const fmtNum = (v?: number | null, digits = 0): string =>
  v == null ? '—' : v.toLocaleString('ru-RU', { maximumFractionDigits: digits, minimumFractionDigits: digits });
/** Порог тревоги в тексте — два знака, русская запись: 0,35 (точное значение — в подсказке, fmtThresholdExact) */
export const fmtThreshold = (v?: number | null): string => (v == null ? '—' : v.toFixed(2).replace('.', ','));
export const fmtThresholdExact = (v?: number | null): string => (v == null ? '—' : String(v).replace('.', ','));

/** Срок для людей: «4 ч», «3 сут» */
export const dueText = (hours: number) => (hours < 48 ? `${Math.round(hours)} ч` : `${Math.round(hours / 24)} сут`);
