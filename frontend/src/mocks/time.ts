// Время в моках — как на бэкенде: московское без пояса. Внутри храним миллисекунды «как будто UTC».
export const MIN = 60_000;
export const HOUR = 60 * MIN;
export const DAY = 24 * HOUR;

/** `2026-08-01T12:00:00` → мс. Строка с поясом переводится в МСК (+03:00), как делает бэкенд. */
export function parseT(s: string): number {
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return Date.parse(`${s}T00:00:00Z`);
  if (/(Z|[+-]\d{2}:?\d{2})$/.test(s)) return Date.parse(s) + 3 * HOUR;
  const full = s.length === 16 ? `${s}:00` : s;
  return Date.parse(`${full}Z`);
}

export const fmtT = (ms: number): string => new Date(ms).toISOString().slice(0, 19);
export const fmtD = (ms: number): string => new Date(ms).toISOString().slice(0, 10);
export const nowMsk = (): number => Math.floor((Date.now() + 3 * HOUR) / 1000) * 1000;
export const dayStart = (ms: number): number => Math.floor(ms / DAY) * DAY;
