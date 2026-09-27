import { useQueryClient } from '@tanstack/react-query';
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { setUnauthorizedHandler, tokenStore } from '@/api/client';
import { api } from '@/api/endpoints';
import type { Permission, UserOut } from '@/types';

interface AuthState {
  user: UserOut | null;
  /** true, пока проверяем сохранённый токен */
  checking: boolean;
  login: (username: string, password: string) => Promise<UserOut>;
  logout: () => void;
  /** Пользователь сам нажал «Выйти»: следующий вход (возможно, под другой ролью) — с дашборда, а не со страницы
   * прежнего пользователя (у техника её может не быть в области видимости) */
  loggedOut: boolean;
  /** Может принимать решения по прогнозам и создавать/менять заявки (право decide) */
  canAct: boolean;
  /** Может менять статус заявки (диспетчеры, техник) */
  canChangeStatus: boolean;
  /** Право из ролевой модели (docs/SECURITY.md); права приходят с сервера — объединение прав всех ролей */
  can: (p: Permission) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [user, setUser] = useState<UserOut | null>(null);
  const [checking, setChecking] = useState<boolean>(() => !!tokenStore.get());
  const [loggedOut, setLoggedOut] = useState(false);

  const logout = useCallback(() => {
    tokenStore.set(null);
    setLoggedOut(true);
    setUser(null);
    qc.clear();
  }, [qc]);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUser(null);
      qc.clear();
    });
  }, [qc]);

  useEffect(() => {
    if (!tokenStore.get()) return;
    api
      .me()
      .then(setUser)
      .catch(() => tokenStore.set(null))
      .finally(() => setChecking(false));
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const res = await api.login({ username, password });
    tokenStore.set(res.access_token);
    setLoggedOut(false);
    setUser(res.user);
    return res.user;
  }, []);

  const value = useMemo<AuthState>(() => {
    const can = (p: Permission) => !!user?.permissions?.includes(p);
    return {
      user,
      checking,
      login,
      logout,
      loggedOut,
      can,
      canAct: can('decide') && can('work_orders'),
      canChangeStatus: can('work_order_status'),
    };
  }, [user, checking, login, logout, loggedOut]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth вне AuthProvider');
  return ctx;
}
