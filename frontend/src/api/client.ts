const TOKEN_KEY = 'collector.token';

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export const tokenStore = {
  get: (): string | null => {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set: (token: string | null) => {
    try {
      if (token) localStorage.setItem(TOKEN_KEY, token);
      else localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* приватный режим — токен живёт до перезагрузки */
    }
  },
};

type Unauthorized = () => void;
let onUnauthorized: Unauthorized = () => {};
export const setUnauthorizedHandler = (fn: Unauthorized) => {
  onUnauthorized = fn;
};

export type QueryValue = string | number | boolean | null | undefined | Array<string | number>;

/** Массивы — повторением ключа (`risk_level=risk&risk_level=critical`), как ждёт FastAPI. Пустые значения пропускаем. */
export function buildQuery(params?: Record<string, QueryValue>): string {
  if (!params) return '';
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v == null || v === '') continue;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, String(x)));
    else sp.append(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
    if (Array.isArray(body.detail))
      return body.detail.map((d: { msg?: string }) => d.msg ?? '').filter(Boolean).join('; ') || res.statusText;
  } catch {
    /* не JSON */
  }
  if (res.status >= 500) return 'Сервер недоступен или вернул ошибку. Попробуйте ещё раз.';
  return res.statusText || `Ошибка ${res.status}`;
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  query?: Record<string, QueryValue>;
  body?: unknown;
  /** Не уводить на экран входа при 401 (для самого входа) */
  skipAuthRedirect?: boolean;
  signal?: AbortSignal;
}

export async function rawRequest(path: string, opts: RequestOptions = {}): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  const token = tokenStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  let res: Response;
  try {
    res = await fetch(`${path}${buildQuery(opts.query)}`, {
      method: opts.method ?? 'GET',
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    throw new ApiError(0, 'Нет связи с сервером. Проверьте сеть и повторите.');
  }
  if (res.status === 401 && !opts.skipAuthRedirect) {
    tokenStore.set(null);
    onUnauthorized();
  }
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return res;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const res = await rawRequest(path, opts);
  return (await res.json()) as T;
}
