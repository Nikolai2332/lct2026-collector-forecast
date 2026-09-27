import { useCallback } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { useDataMomentDefaults } from './dataMoment';

export { useDataMomentDefaults } from './dataMoment';

const AT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/;

/** Момент, явно выбранный в «машине времени» (`?at=` в URL); undefined — режим «Сейчас». */
export function useUrlAt(): string | undefined {
  const [params] = useSearchParams();
  const raw = params.get('at') ?? undefined;
  return raw && AT_RE.test(raw) ? (raw.length === 16 ? `${raw}:00` : raw) : undefined;
}

/**
 * «Машина времени»: момент `at` для запросов к API. Явный — из URL (`?at=2026-06-15T12:00:00`),
 * иначе — момент по умолчанию (см. DataMomentProvider) или undefined, если данные свежие.
 */
export function useAt(): [string | undefined, (at: string | undefined) => void] {
  const [, setParams] = useSearchParams();
  const urlAt = useUrlAt();
  const { defaultAt } = useDataMomentDefaults();
  const setAt = useCallback(
    (next: string | undefined) => {
      setParams(
        (prev) => {
          const p = new URLSearchParams(prev);
          if (next) p.set('at', next);
          else p.delete('at');
          return p;
        },
        { replace: false },
      );
    },
    [setParams],
  );
  return [urlAt ?? defaultAt, setAt];
}

/** Ссылка на другой экран с сохранением явно выбранного `?at=` */
export function useAtHref() {
  const at = useUrlAt();
  return useCallback(
    (path: string) => {
      if (!at) return path;
      return `${path}${path.includes('?') ? '&' : '?'}at=${encodeURIComponent(at)}`;
    },
    [at],
  );
}

export function useAtNavigate() {
  const navigate = useNavigate();
  const href = useAtHref();
  return useCallback((path: string) => navigate(href(path)), [navigate, href]);
}

export function useCurrentPath() {
  return useLocation().pathname;
}
