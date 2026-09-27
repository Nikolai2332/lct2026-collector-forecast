import { EditOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Checkbox, Form, Modal, Space, Table, Tag, Tooltip, TreeSelect, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMemo, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { api } from '@/api/endpoints';
import { QueryState } from '@/components/QueryState';
import { useAuth } from '@/hooks/useAuth';
import type { ObjectNode, Role, UserOut } from '@/types';

/** Роли заказчика (docs/SECURITY.md): подписи, область и права — для формы и подсказки */
const ROLE_INFO: { role: Role; label: string; scoped: boolean; hint: string }[] = [
  { role: 'dispatcher_ods', label: 'Диспетчер ОДС', scoped: false, hint: 'все объекты; решения, заявки, симуляция' },
  { role: 'dispatcher', label: 'Диспетчер района', scoped: true, hint: 'дерево района; решения и заявки' },
  { role: 'technician', label: 'Техник', scoped: true, hint: 'один комплекс; только смена статуса заявок' },
  { role: 'manager', label: 'Руководитель', scoped: true, hint: 'чтение и выгрузки в своей области' },
  { role: 'engineer', label: 'Инженер данных', scoped: false, hint: 'все объекты; импорт данных, симуляция' },
  { role: 'admin', label: 'Администратор', scoped: false, hint: 'всё, настройки, роли и области' },
];
const SCOPED = new Set(ROLE_INFO.filter((r) => r.scoped).map((r) => r.role));
const GLOBAL = new Set(ROLE_INFO.filter((r) => !r.scoped).map((r) => r.role));

interface TreeOption {
  value: number;
  title: string;
  children?: TreeOption[];
}

/** Область назначается районом (уровень 1) или объектом-комплексом (уровень 2) — со всем поддеревом */
function toOptions(nodes: ObjectNode[]): TreeOption[] {
  return nodes
    .filter((n) => n.level <= 2)
    .map((n) => ({ value: n.id, title: n.name, children: n.children?.length ? toOptions(n.children) : undefined }));
}

interface FormValues {
  roles: Role[];
  scope: number[];
}

function AccessModal({ user, onClose }: { user: UserOut; onClose: () => void }) {
  const [form] = Form.useForm<FormValues>();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const tree = useQuery({ queryKey: ['objects-admin'], queryFn: () => api.objects({}) });
  const options = useMemo(() => toOptions(tree.data?.items ?? []), [tree.data]);
  const roles = Form.useWatch('roles', form) ?? user.roles;
  const needsScope = roles.some((r) => SCOPED.has(r)) && !roles.some((r) => GLOBAL.has(r));
  const save = useMutation({
    mutationFn: (v: FormValues) => api.setUserAccess(user.id, { roles: v.roles, scope_object_ids: v.scope ?? [] }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['users'] });
      message.success('Роли и область сохранены; действуют со следующего запроса пользователя');
      onClose();
    },
    onError: (e) => message.error(e.message),
  });
  return (
    <Modal
      open
      title={`Роли и область: ${user.full_name} (${user.username})`}
      okText="Сохранить"
      cancelText="Отмена"
      onCancel={onClose}
      onOk={() => form.submit()}
      confirmLoading={save.isPending}
      width={620}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{ roles: user.roles, scope: user.scope.map((s) => s.id) }}
        onFinish={(v) => save.mutate(v)}
      >
        <Form.Item name="roles" label="Роли (складываются)" rules={[{ required: true, message: 'Нужна хотя бы одна роль' }]}>
          <Checkbox.Group style={{ display: 'grid', gap: 6 }}>
            {ROLE_INFO.map((r) => (
              <Checkbox key={r.role} value={r.role}>
                <b>{r.label}</b> <Typography.Text type="secondary">— {r.hint}</Typography.Text>
              </Checkbox>
            ))}
          </Checkbox.Group>
        </Form.Item>
        <Form.Item
          name="scope"
          label="Область видимости (район или объект-комплекс)"
          extra={
            needsScope
              ? 'Обязательна: без области роль не увидит ни одного датчика.'
              : 'Для ролей «видит всё» не используется, но сохранится на случай снятия этих ролей.'
          }
          rules={[{ required: needsScope, message: 'Выберите район или объект' }]}
        >
          <TreeSelect
            treeData={options}
            multiple
            treeCheckable
            showCheckedStrategy={TreeSelect.SHOW_PARENT}
            treeCheckStrictly={false}
            placeholder="Например, объект Альфа"
            loading={tree.isLoading}
            treeDefaultExpandAll
            style={{ width: '100%' }}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}

export default function UsersPage() {
  const { can, user: me } = useAuth();
  const query = useQuery({ queryKey: ['users'], queryFn: api.users, enabled: can('users_admin') });
  const [editing, setEditing] = useState<UserOut | null>(null);
  if (!can('users_admin')) return <Navigate to="/dashboard" replace />;

  const columns: ColumnsType<UserOut> = [
    {
      title: 'Пользователь',
      render: (_, u) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{u.full_name}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {u.username} · {u.auth_source === 'ldap' ? 'LDAP/AD' : 'локальная'}
            {!u.is_active && ' · заблокирован'}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: 'Роли',
      render: (_, u) => (
        <Space size={[4, 4]} wrap>
          {u.roles.map((r) => (
            <Tag key={r} color={GLOBAL.has(r) ? 'blue' : 'default'}>
              {ROLE_INFO.find((x) => x.role === r)?.label ?? r}
            </Tag>
          ))}
        </Space>
      ),
    },
    { title: 'Область видимости', dataIndex: 'scope_label' },
    {
      title: '',
      width: 140,
      render: (_, u) =>
        u.auth_source === 'ldap' ? (
          <Tooltip title="Роли и область учётки из каталога задают группы AD (LDAP_ROLE_GROUPS, LDAP_SCOPE_GROUPS)">
            <Typography.Text type="secondary">из групп AD</Typography.Text>
          </Tooltip>
        ) : (
          <Button size="small" icon={<EditOutlined />} onClick={() => setEditing(u)}>
            Изменить
          </Button>
        ),
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        Пользователи и роли
      </Typography.Title>
      <Alert
        type="info"
        showIcon
        message="Ролевая модель заказчика: роли складываются, область — объединение выбранных районов и комплексов"
        description={
          <>
            Диспетчер ОДС видит всё; диспетчер района — дерево своего района; техник — один комплекс; руководитель —
            чтение и отчёты в своей области. В production учётные записи ведутся в AD: роли и области приходят из групп
            каталога, здесь — только локальные учётки (демо без AD). Каждое изменение пишется в журнал действий.
          </>
        }
      />
      <Card>
        <QueryState query={query}>
          {(data) => (
            <Table
              rowKey="id"
              size="middle"
              columns={columns}
              dataSource={data.items}
              pagination={false}
              rowClassName={(u) => (u.id === me?.id ? 'row-me' : '')}
            />
          )}
        </QueryState>
      </Card>
      {editing && <AccessModal user={editing} onClose={() => setEditing(null)} />}
    </Space>
  );
}
