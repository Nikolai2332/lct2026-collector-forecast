import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp } from 'antd';
import dayjs from 'dayjs';
import 'dayjs/locale/ru';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { ApiError } from '@/api/client';
import { App } from './App';
import { AuthProvider } from './hooks/useAuth';
import { ThemeRoot } from './hooks/useThemeMode';
import { USE_MOCKS } from './utils/config';
import './index.css';

dayjs.locale('ru');

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      // 4xx повторять бессмысленно
      retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
    },
  },
});

async function enableMocks() {
  if (!USE_MOCKS) return;
  const { worker } = await import('./mocks/browser');
  await worker.start({ onUnhandledRequest: 'bypass', quiet: true, serviceWorker: { url: '/mockServiceWorker.js' } });
}

enableMocks().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <ThemeRoot
          render={() => (
            <AntApp>
              <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
                <AuthProvider>
                  <App />
                </AuthProvider>
              </BrowserRouter>
            </AntApp>
          )}
        />
      </QueryClientProvider>
    </StrictMode>,
  );
});
