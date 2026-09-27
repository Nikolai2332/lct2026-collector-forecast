import {
  AlertOutlined,
  ApartmentOutlined,
  DashboardOutlined,
  FileTextOutlined,
  LineChartOutlined,
  LogoutOutlined,
  MoonOutlined,
  SunOutlined,
  ProfileOutlined,
  SettingOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { Avatar, Button, Dropdown, Layout, Menu, Space, Tag, Tooltip, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { useAtNavigate } from '@/hooks/useAt';
import { useAuth } from '@/hooks/useAuth';
import { useThemeMode } from '@/hooks/useThemeMode';
import { USE_MOCKS } from '@/utils/config';
import { ErrorBoundary } from './ErrorBoundary';
import { NotificationBell } from './NotificationBell';
import { SettingsDrawer } from './SettingsDrawer';
import { SimulationControl } from './SimulationControl';
import { TimeMachine } from './TimeMachine';

const { Header, Sider, Content } = Layout;

const MENU = [
  { key: '/dashboard', icon: <DashboardOutlined />, label: 'Дашборд' },
  { key: '/objects', icon: <ApartmentOutlined />, label: 'Схема объектов' },
  { key: '/journal', icon: <ProfileOutlined />, label: 'Журнал' },
  { key: '/work-orders', icon: <FileTextOutlined />, label: 'Заявки' },
  { key: '/quality', icon: <LineChartOutlined />, label: 'Качество модели' },
];

export function AppLayout() {
  const { user, logout, can } = useAuth();
  const { mode, toggle } = useThemeMode();
  const nav = useAtNavigate();
  const { pathname } = useLocation();
  const [collapsed, setCollapsed] = useState(() => window.innerWidth < 1440);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const selected = MENU.find((m) => pathname.startsWith(m.key))?.key ?? (pathname.startsWith('/channels') ? '/objects' : '');
  // Заголовок вкладки — экран и система: несколько открытых вкладок различимы
  const screen = pathname.startsWith('/channels')
    ? 'Карточка датчика'
    : /^\/work-orders\/\d+/.test(pathname)
      ? 'Заявка'
      : pathname.startsWith('/admin/users')
        ? 'Пользователи и роли'
        : MENU.find((m) => pathname.startsWith(m.key))?.label;
  // «Техник · объект Альфа»: роли и область — чтобы было видно, чьими глазами смотрит пользователь
  const roleLine = user ? `${user.role_label} · ${user.scope_label}` : '';
  useEffect(() => {
    document.title = screen ? `${screen} — Прогноз отказов коллекторов` : 'Прогноз отказов коллекторов';
  }, [screen]);

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header className="app-header no-print">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
          <AlertOutlined style={{ fontSize: 22, color: '#1677ff' }} />
          <span className="app-title">Прогноз отказов коллекторов</span>
          {USE_MOCKS && (
            <Tooltip title="Данные из моков MSW. Переключение на реальный API: VITE_USE_MOCKS=false или config.js">
              <Tag color="gold">Демо-данные</Tag>
            </Tooltip>
          )}
        </div>
        <Space size={16} wrap={false}>
          <SimulationControl />
          <TimeMachine />
          <NotificationBell />
          <Tooltip title={mode === 'dark' ? 'Светлая тема' : 'Тёмная тема (ночная смена)'}>
            <Button
              type="text"
              icon={mode === 'dark' ? <SunOutlined /> : <MoonOutlined />}
              onClick={toggle}
              aria-label={mode === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему'}
            />
          </Tooltip>
          <Dropdown
            trigger={['click']}
            menu={{
              items: [
                ...(can('users_admin') ? [{ key: 'users', icon: <TeamOutlined />, label: 'Пользователи и роли' }] : []),
                ...(can('settings') ? [{ key: 'settings', icon: <SettingOutlined />, label: 'Настройки' }] : []),
                { key: 'logout', icon: <LogoutOutlined />, label: 'Выйти' },
              ],
              onClick: ({ key }) => (key === 'settings' ? setSettingsOpen(true) : key === 'users' ? nav('/admin/users') : logout()),
            }}
          >
            <Button type="text" aria-label="Меню пользователя" style={{ height: 'auto', padding: '2px 6px' }}>
              <Space size={8}>
                <Avatar size="small" icon={<UserOutlined />} style={{ background: '#1677ff' }} />
                <span className="app-user">
                  <Typography.Text strong>{user?.full_name}</Typography.Text>
                  <Tooltip title={`Роли: ${user?.role_label}. Область видимости: ${user?.scope_label}`}>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }} className="app-user-role">
                      {roleLine}
                    </Typography.Text>
                  </Tooltip>
                </span>
              </Space>
            </Button>
          </Dropdown>
          <Tooltip title="Выйти">
            <Button icon={<LogoutOutlined />} onClick={logout} aria-label="Выйти" />
          </Tooltip>
        </Space>
      </Header>
      <Layout>
        <Sider
          className="no-print"
          theme="light"
          width={220}
          collapsible
          collapsed={collapsed}
          onCollapse={setCollapsed}
          style={{ borderRight: '1px solid var(--app-border)' }}
        >
          <Menu mode="inline" selectedKeys={[selected]} items={MENU} onClick={(e) => nav(e.key)} style={{ borderInlineEnd: 0 }} />
        </Sider>
        <Content className="app-content">
          <ErrorBoundary resetKey={pathname}>
            <Outlet />
          </ErrorBoundary>
        </Content>
      </Layout>
      {can('settings') && <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />}
    </Layout>
  );
}
