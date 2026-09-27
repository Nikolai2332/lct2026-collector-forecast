import { Spin } from 'antd';
import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { AppLayout } from '@/components/AppLayout';
import { DataMomentProvider } from '@/components/DataMomentProvider';
import { useAuth } from '@/hooks/useAuth';
import { NotificationsProvider } from '@/hooks/useNotifications';
import { LoginPage } from '@/pages/LoginPage';

const DashboardPage = lazy(() => import('@/pages/DashboardPage'));
const ObjectsPage = lazy(() => import('@/pages/ObjectsPage'));
const ChannelPage = lazy(() => import('@/pages/ChannelPage'));
const JournalPage = lazy(() => import('@/pages/JournalPage'));
const WorkOrdersPage = lazy(() => import('@/pages/WorkOrdersPage'));
const WorkOrderPage = lazy(() => import('@/pages/WorkOrderPage'));
const QualityPage = lazy(() => import('@/pages/QualityPage'));
const UsersPage = lazy(() => import('@/pages/UsersPage'));

const Loading = () => (
  <div style={{ padding: 48, textAlign: 'center' }}>
    <Spin size="large" />
  </div>
);

function RequireAuth() {
  const { user, checking, loggedOut } = useAuth();
  const loc = useLocation();
  if (checking) return <Loading />;
  // Истёк токен или прямая ссылка — после входа вернуть сюда; явный выход — не запоминать страницу
  if (!user) return <Navigate to="/login" replace state={loggedOut ? null : { from: loc.pathname + loc.search }} />;
  return (
    <DataMomentProvider>
      <NotificationsProvider>
        <AppLayout />
      </NotificationsProvider>
    </DataMomentProvider>
  );
}

export function App() {
  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/objects" element={<ObjectsPage />} />
          <Route path="/channels/:id" element={<ChannelPage />} />
          <Route path="/journal" element={<JournalPage />} />
          <Route path="/work-orders" element={<WorkOrdersPage />} />
          <Route path="/work-orders/:id" element={<WorkOrderPage />} />
          <Route path="/quality" element={<QualityPage />} />
          <Route path="/admin/users" element={<UsersPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </Suspense>
  );
}
