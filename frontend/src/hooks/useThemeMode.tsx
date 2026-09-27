import { ConfigProvider, theme } from 'antd';
import ruRU from 'antd/locale/ru_RU';
import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from 'react';
import { applyRiskPalette } from '@/utils/risk';

/** Светлая и тёмная тема: диспетчер работает круглосуточно. Выбор запоминается в браузере (localStorage). */
export type ThemeMode = 'light' | 'dark';

const KEY = 'collector-ui-theme';

function initialMode(): ThemeMode {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === 'light' || saved === 'dark') return saved;
  } catch {
    /* приватный режим — просто светлая тема */
  }
  return 'light';
}

function apply(mode: ThemeMode) {
  document.documentElement.dataset.theme = mode;
  document.documentElement.style.colorScheme = mode;
  applyRiskPalette(mode);
}

interface ThemeState {
  mode: ThemeMode;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeState>({ mode: 'light', toggle: () => undefined });

/** Корень темы. Всё приложение передаётся фабрикой render() и создаётся здесь заново при смене темы — так
 * перерисовываются и собственные компоненты с цветами риска, и графики, а не только компоненты Ant Design. */
export function ThemeRoot({ render }: { render: () => ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(() => {
    const m = initialMode();
    apply(m);
    return m;
  });
  const toggle = useCallback(() => {
    setMode((prev) => {
      const next: ThemeMode = prev === 'dark' ? 'light' : 'dark';
      apply(next);
      try {
        localStorage.setItem(KEY, next);
      } catch {
        /* не сохранилось — тема действует до перезагрузки */
      }
      return next;
    });
  }, []);
  const value = useMemo(() => ({ mode, toggle }), [mode, toggle]);
  return (
    <ThemeContext.Provider value={value}>
      <ConfigProvider
        locale={ruRU}
        theme={{
          algorithm: mode === 'dark' ? theme.darkAlgorithm : theme.defaultAlgorithm,
          token: { colorPrimary: '#1677ff', borderRadius: 6, fontSize: 14 },
        }}
      >
        {render()}
      </ConfigProvider>
    </ThemeContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useThemeMode(): ThemeState {
  return useContext(ThemeContext);
}
