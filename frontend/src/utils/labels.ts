import type { DecisionType, Role, WorkOrderPriority, WorkOrderStatus } from '@/types';

export const DECISION_LABELS: Record<DecisionType, string> = {
  dispatch: 'Выезд бригады',
  false_alarm: 'Ложное срабатывание',
  monitor: 'Наблюдение',
};
export const DECISION_COLORS: Record<DecisionType, string> = {
  dispatch: 'volcano',
  false_alarm: 'default',
  monitor: 'blue',
};

export const WO_STATUS_LABELS: Record<WorkOrderStatus, string> = {
  draft: 'Черновик',
  submitted: 'Отправлена',
  in_progress: 'В работе',
  done: 'Выполнена',
  cancelled: 'Отменена',
};
export const WO_STATUS_COLORS: Record<WorkOrderStatus, string> = {
  draft: 'default',
  submitted: 'processing',
  in_progress: 'orange',
  done: 'success',
  cancelled: 'error',
};
export const WO_OPEN_STATUSES: WorkOrderStatus[] = ['draft', 'submitted', 'in_progress'];

/** Разрешённые переходы — как в backend/app/labels.py */
export const WO_TRANSITIONS: Record<WorkOrderStatus, WorkOrderStatus[]> = {
  draft: ['submitted', 'cancelled'],
  submitted: ['in_progress', 'draft', 'cancelled'],
  in_progress: ['done', 'cancelled'],
  done: [],
  cancelled: [],
};

export const WO_PRIORITY_LABELS: Record<WorkOrderPriority, string> = {
  low: 'Низкий',
  medium: 'Средний',
  high: 'Высокий',
  critical: 'Аварийный',
};
export const WO_PRIORITY_COLORS: Record<WorkOrderPriority, string> = {
  low: 'default',
  medium: 'gold',
  high: 'orange',
  critical: 'red',
};
/** Срок по приоритету — как на бэкенде: 4 ч / 24 ч / 3 дня / 7 дней */
export const WO_PRIORITY_DUE_HOURS: Record<WorkOrderPriority, number> = {
  critical: 4,
  high: 24,
  medium: 72,
  low: 168,
};

export const ROLE_LABELS: Record<Role, string> = {
  dispatcher_ods: 'Диспетчер ОДС',
  dispatcher: 'Диспетчер района',
  technician: 'Техник',
  engineer: 'Инженер данных',
  manager: 'Руководитель',
  admin: 'Администратор',
};
