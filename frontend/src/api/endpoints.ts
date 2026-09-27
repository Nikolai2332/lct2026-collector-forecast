import type {
  ChannelCard,
  ChannelHistory,
  ChannelList,
  ChannelsQuery,
  DashboardSummary,
  EventList,
  EventsQuery,
  GeoChannels,
  GeoCollectors,
  DecisionIn,
  Dictionaries,
  HealthOut,
  HistoryQuery,
  JournalList,
  JournalQuery,
  LoginIn,
  MaintenanceAdvice,
  MaintenanceDraftsOut,
  MaintenancePlan,
  MetricsQuery,
  ModelMetrics,
  NotificationList,
  NotificationsQuery,
  ObjectDetail,
  ObjectDetailQuery,
  ObjectTree,
  PredictionDetail,
  PredictionList,
  PredictionsQuery,
  ReasonOut,
  SettingsIn,
  SettingsOut,
  RecommendationOut,
  SimStartIn,
  SimStateOut,
  SseTicketOut,
  ThresholdTable,
  TokenOut,
  UserAccessIn,
  UserList,
  UserOut,
  WorkOrderIn,
  WorkOrderList,
  WorkOrderOut,
  WorkOrderPatch,
  WorkOrdersQuery,
} from '@/types';
import { type QueryValue, rawRequest, request } from './client';

type Q = Record<string, QueryValue>;
const q = (x: object | undefined): Q => (x ?? {}) as Q;

export const api = {
  login: (body: LoginIn) =>
    request<TokenOut>('/api/auth/login', { method: 'POST', body, skipAuthRedirect: true }),
  me: () => request<UserOut>('/api/auth/me'),

  dictionaries: () => request<Dictionaries>('/api/dictionaries'),
  reasons: (decision_type?: string) => request<ReasonOut[]>('/api/reasons', { query: { decision_type } }),
  recommendations: (sensor_type?: string) =>
    request<RecommendationOut[]>('/api/recommendations', { query: { sensor_type } }),

  summary: (at?: string) => request<DashboardSummary>('/api/dashboard/summary', { query: { at } }),
  objects: (query: { at?: string; system_type?: string; sensor_type?: string }) =>
    request<ObjectTree>('/api/objects', { query: q(query) }),
  object: (id: number, query: ObjectDetailQuery) => request<ObjectDetail>(`/api/objects/${id}`, { query: q(query) }),

  channels: (query: ChannelsQuery) => request<ChannelList>('/api/channels', { query: q(query) }),
  channel: (id: number, at?: string) => request<ChannelCard>(`/api/channels/${id}`, { query: { at } }),
  channelHistory: (id: number, query: HistoryQuery) =>
    request<ChannelHistory>(`/api/channels/${id}/history`, { query: q(query) }),

  predictions: (query: PredictionsQuery) => request<PredictionList>('/api/predictions', { query: q(query) }),
  prediction: (id: number, at?: string) => request<PredictionDetail>(`/api/predictions/${id}`, { query: { at } }),
  /** Рекомендация по ТО по правилам (не ML) */
  recommendation: (id: number, at?: string) =>
    request<MaintenanceAdvice>(`/api/predictions/${id}/recommendation`, { query: { at } }),
  maintenancePlan: (query: { at?: string; object_id?: number; limit?: number; offset?: number }) =>
    request<MaintenancePlan>('/api/maintenance/plan', { query: q(query) }),
  maintenanceDrafts: (prediction_ids: number[], at?: string) =>
    request<MaintenanceDraftsOut>('/api/maintenance/drafts', { method: 'POST', body: { prediction_ids }, query: { at } }),
  decide: (id: number, body: DecisionIn, at?: string) =>
    request<PredictionDetail>(`/api/predictions/${id}/decision`, { method: 'POST', body, query: { at } }),

  journal: (query: JournalQuery) => request<JournalList>('/api/journal', { query: q(query) }),
  exportJournal: (query: Omit<JournalQuery, 'limit' | 'offset'>) =>
    rawRequest('/api/export/journal', { query: q(query) }),

  workOrders: (query: WorkOrdersQuery) => request<WorkOrderList>('/api/work-orders', { query: q(query) }),
  workOrder: (id: number, at?: string) => request<WorkOrderOut>(`/api/work-orders/${id}`, { query: { at } }),
  createWorkOrder: (body: WorkOrderIn, at?: string) =>
    request<WorkOrderOut>('/api/work-orders', { method: 'POST', body, query: { at } }),
  patchWorkOrder: (id: number, body: WorkOrderPatch, at?: string) =>
    request<WorkOrderOut>(`/api/work-orders/${id}`, { method: 'PATCH', body, query: { at } }),

  metrics: (query: MetricsQuery) => request<ModelMetrics>('/api/model/metrics', { query: q(query) }),
  thresholds: (at?: string) => request<ThresholdTable>('/api/model/thresholds', { query: { at } }),

  notifications: (query: NotificationsQuery) =>
    request<NotificationList>('/api/notifications', { query: q(query) }),
  /** Одноразовый тикет для EventSource: JWT в адрес потока не кладём */
  sseTicket: () => request<SseTicketOut>('/api/notifications/ticket', { method: 'POST' }),

  /** Настраиваемые параметры: читать может любая роль, менять — администратор */
  settings: () => request<SettingsOut>('/api/settings'),
  saveSettings: (body: SettingsIn) => request<SettingsOut>('/api/settings', { method: 'PUT', body }),

  /** Схема коллекторов: синтетическая геометрия по пикетам (GeoJSON, условные метры) */
  geoCollectors: (query: { at?: string; object_id?: number }) =>
    request<GeoCollectors>('/api/geo/collectors', { query: q(query) }),
  geoChannels: (query: { at?: string; object_id?: number; system_type?: string; sensor_type?: string }) =>
    request<GeoChannels>('/api/geo/channels', { query: q(query) }),

  /** Журнал тревожных событий с контекстом (правила, не ML) */
  events: (query: EventsQuery) => request<EventList>('/api/events', { query: q(query) }),
  exportEvents: (query: Omit<EventsQuery, 'limit' | 'offset'>) => rawRequest('/api/export/events', { query: q(query) }),

  /** Роли и области локальных пользователей (администратор) */
  users: () => request<UserList>('/api/users'),
  setUserAccess: (id: number, body: UserAccessIn) =>
    request<UserOut>(`/api/users/${id}/access`, { method: 'PUT', body }),

  health: () => request<HealthOut>('/api/health', { skipAuthRedirect: true }),

  /** Симуляция потока: проигрывание суток по заранее рассчитанным прогнозам */
  simState: () => request<SimStateOut>('/api/sim/state'),
  simStart: (body: SimStartIn) => request<SimStateOut>('/api/sim/start', { method: 'POST', body }),
  simStop: () => request<SimStateOut>('/api/sim/stop', { method: 'POST' }),
};
