import { BellOutlined, SoundOutlined } from '@ant-design/icons';
import { Badge, Button, Empty, List, Popover, Space, Switch, Tooltip, Typography } from 'antd';
import { useAtNavigate } from '@/hooks/useAt';
import { useNotifications } from '@/hooks/useNotifications';
import { humanFactors } from '@/utils/factors';
import { notificationPath } from '@/utils/notifications';
import { fmtDateTime } from '@/utils/time';
import { RiskBadge } from './RiskBadge';

/** Колокольчик: новые переходы датчиков в «Критично» (сводки по срезам), выключатель звука */
export function NotificationBell() {
  const { items, unread, markRead, soundOn, setSoundOn, connected } = useNotifications();
  const nav = useAtNavigate();
  const content = (
    <div style={{ width: 'min(400px, calc(100vw - 32px))' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 8 }}>
        <Space size={6}>
          <SoundOutlined />
          <span>Звук</span>
          <Switch size="small" checked={soundOn} onChange={setSoundOn} aria-label="Звук уведомлений" />
        </Space>
        <Button size="small" type="link" onClick={markRead} disabled={!unread}>
          Прочитать все
        </Button>
      </Space>
      {items.length ? (
        <List
          size="small"
          dataSource={items.slice(0, 40)}
          style={{ maxHeight: 420, overflow: 'auto' }}
          renderItem={(n) => {
            const summary = n.kind === 'critical_summary';
            const factor = summary ? null : humanFactors(n.prediction)[0]?.short;
            return (
              <List.Item key={`${n.kind}|${n.id}|${n.created_at}`} style={{ cursor: 'pointer' }} onClick={() => nav(notificationPath(n))}>
                <List.Item.Meta
                  avatar={<RiskBadge level={n.prediction.risk_level} iconOnly />}
                  title={summary ? <Typography.Link>{n.title} →</Typography.Link> : n.title}
                  description={
                    <>
                      {summary ? 'Открыть критичные этого среза в журнале' : n.message}
                      {factor && (
                        <>
                          <br />
                          {factor}
                        </>
                      )}
                      <br />
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {fmtDateTime(n.created_at)}
                      </Typography.Text>
                    </>
                  }
                />
              </List.Item>
            );
          }}
        />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Новых критичных за сутки нет" />
      )}
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Только новые: датчик перешёл в «Критично» и не был в нём сутки до этого.{' '}
        {connected ? 'Поток уведомлений подключён.' : 'Подключение к потоку уведомлений…'}
      </Typography.Text>
    </div>
  );
  return (
    <Popover content={content} title="Новые критичные датчики" trigger="click" placement="bottomRight" onOpenChange={(o) => !o && markRead()}>
      <Tooltip title="Уведомления">
        <Badge count={unread} overflowCount={99}>
          <Button icon={<BellOutlined />} aria-label={`Уведомления: непрочитанных ${unread}`} />
        </Badge>
      </Tooltip>
    </Popover>
  );
}
