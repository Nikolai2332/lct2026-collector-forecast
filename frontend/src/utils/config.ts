declare global {
  interface Window {
    __APP_CONFIG__?: { useMocks?: boolean | string | null };
  }
}

function parseBool(v: unknown): boolean | null {
  if (typeof v === 'boolean') return v;
  if (typeof v === 'string' && v.trim() !== '') return !['false', '0', 'no', 'off'].includes(v.trim().toLowerCase());
  return null;
}

/** Моки включены по умолчанию. Рантайм-конфиг (public/config.js) важнее переменной сборки VITE_USE_MOCKS. */
export const USE_MOCKS: boolean =
  parseBool(window.__APP_CONFIG__?.useMocks) ?? parseBool(import.meta.env.VITE_USE_MOCKS) ?? true;
