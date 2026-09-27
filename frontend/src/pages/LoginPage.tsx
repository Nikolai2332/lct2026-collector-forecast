import { AlertOutlined, LockOutlined, UserOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Divider, Form, Input, Space, Tooltip, Typography } from 'antd';
import { useState } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { useHealth } from '@/api/queries';
import { useAuth } from '@/hooks/useAuth';
import { USE_MOCKS } from '@/utils/config';

/** Демо-пользователи из README (только APP_ENV=demo): роли заказчика и их области видимости */
const DEMO_USERS = [
  { username: 'ods', password: 'ods123', label: 'Диспетчер ОДС', hint: 'центральная диспетчерская — все объекты' },
  { username: 'dispatcher', password: 'dispatcher123', label: 'Диспетчер района', hint: 'дерево своего района' },
  { username: 'technician', password: 'technician123', label: 'Техник', hint: 'один комплекс, только смена статуса заявок' },
  { username: 'manager', password: 'manager123', label: 'Руководитель', hint: 'чтение и отчёты по району' },
  { username: 'engineer', password: 'engineer123', label: 'Инженер данных', hint: 'импорт данных и симуляция' },
  { username: 'admin', password: 'admin123', label: 'Администратор', hint: 'настройки, роли и области' },
];

interface FormValues {
  username: string;
  password: string;
}

export function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const loc = useLocation();
  const [form] = Form.useForm<FormValues>();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Быстрый выбор роли — только когда бэкенд в демо-режиме (APP_ENV=demo) или на моках.
  // В production демо-учётки заблокированы, и кнопки не показываются
  const health = useHealth();
  const demo = USE_MOCKS || health.data?.demo === true;
  const from = (loc.state as { from?: string } | null)?.from ?? '/dashboard';

  if (user) return <Navigate to={from} replace />;

  const submit = async (v: FormValues) => {
    setError(null);
    setLoading(true);
    try {
      await login(v.username, v.password);
      navigate(from, { replace: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось войти');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'grid',
        placeItems: 'center',
        background: 'var(--login-bg)',
      }}
    >
      <Card style={{ width: 420, maxWidth: 'calc(100vw - 32px)' }}>
        <Space align="center" size={12} style={{ marginBottom: 8 }}>
          <AlertOutlined style={{ fontSize: 32, color: '#1677ff' }} />
          <Typography.Title level={3} style={{ margin: 0 }}>
            Прогноз отказов коллекторов
          </Typography.Title>
        </Space>
        <Typography.Paragraph type="secondary">
          Интерфейс диспетчера подземных коллекторов: какие датчики откажут в ближайшие 24 часа, почему модель так
          считает и заявка на выезд — на одном экране. Войдите, чтобы продолжить.
        </Typography.Paragraph>
        {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
        <Form form={form} layout="vertical" onFinish={submit} requiredMark={false}>
          <Form.Item name="username" label="Логин" rules={[{ required: true, message: 'Введите логин' }]}>
            <Input prefix={<UserOutlined />} autoComplete="username" autoFocus />
          </Form.Item>
          <Form.Item name="password" label="Пароль" rules={[{ required: true, message: 'Введите пароль' }]}>
            <Input.Password prefix={<LockOutlined />} autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={loading}>
            Войти
          </Button>
        </Form>
        {demo && (
          <>
            <Divider plain>Демо: быстрый выбор роли</Divider>
            <Space wrap style={{ width: '100%', justifyContent: 'center' }}>
              {DEMO_USERS.map((u) => (
                <Tooltip key={u.username} title={u.hint}>
                  <Button
                    size="small"
                    onClick={() => {
                      form.setFieldsValue({ username: u.username, password: u.password });
                      form.submit();
                    }}
                  >
                    {u.label}
                  </Button>
                </Tooltip>
              ))}
            </Space>
          </>
        )}
        {!demo && (
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 16, marginBottom: 0 }}>
            Нет учётной записи или забыли пароль — обратитесь к администратору системы.
          </Typography.Paragraph>
        )}
        {USE_MOCKS && (
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 16, marginBottom: 0 }}>
            Работа на демо-данных (моки MSW).
          </Typography.Paragraph>
        )}
      </Card>
    </div>
  );
}
