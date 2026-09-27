import { ReloadOutlined, SmileOutlined } from '@ant-design/icons';
import type { UseQueryResult } from '@tanstack/react-query';
import { Button, Empty, Result, Skeleton } from 'antd';
import type { ReactNode } from 'react';

interface Props<T> {
  query: Pick<UseQueryResult<T>, 'data' | 'isPending' | 'isError' | 'error' | 'refetch' | 'isFetching'>;
  children: (data: T) => ReactNode;
  /** Пусто ли — тогда показываем заглушку вместо children */
  isEmpty?: (data: T) => boolean;
  emptyText?: ReactNode;
  /** Строк скелетона */
  rows?: number;
  /** Вместо скелетона-абзаца — прямоугольник заданной высоты (графики, плитки) */
  skeletonHeight?: number;
  compact?: boolean;
}

/** Три состояния любого экрана: загрузка (скелетон), пусто, ошибка с «Повторить». */
export function QueryState<T>({
  query,
  children,
  isEmpty,
  emptyText = 'Рисков не обнаружено',
  rows = 6,
  skeletonHeight,
  compact,
}: Props<T>) {
  if (query.isPending) {
    return (
      <div data-state="loading" aria-busy="true">
        {skeletonHeight ? (
          <Skeleton.Node active style={{ width: '100%', height: skeletonHeight }}>
            <span />
          </Skeleton.Node>
        ) : (
          <Skeleton active paragraph={{ rows }} title={!compact} />
        )}
      </div>
    );
  }
  if (query.isError) {
    return (
      <Result
        data-state="error"
        status="warning"
        title={compact ? undefined : 'Не удалось загрузить данные'}
        subTitle={query.error instanceof Error ? query.error.message : 'Неизвестная ошибка'}
        style={compact ? { padding: 16 } : undefined}
        extra={
          <Button type="primary" icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => query.refetch()}>
            Повторить
          </Button>
        }
      />
    );
  }
  const data = query.data as T;
  if (isEmpty?.(data)) {
    return (
      <Empty
        data-state="empty"
        image={<SmileOutlined style={{ fontSize: compact ? 32 : 48, color: '#52c41a' }} />}
        styles={{ image: { height: compact ? 36 : 56 } }}
        description={emptyText}
        style={{ padding: compact ? 12 : 32 }}
      />
    );
  }
  return <>{children(data)}</>;
}
