/** Изменяемое состояние моков (решения и заявки) и сборка ответов по форме openapi.json. */
import type {
  ChannelListItem,
  ChannelRef,
  DecisionOut,
  DecisionType,
  PredictionDetail,
  PredictionItem,
  WorkOrderOut,
  WorkOrderPriority,
  WorkOrderShort,
  WorkOrderStatus,
} from '@/types';
import {
  CHANNELS,
  DATA_END,
  MODEL_VERSION,
  type MockChannel,
  REASONS,
  RECOMMENDATIONS,
  SNAPSHOTS,
  USERS,
  decodePrediction,
  factorsOf,
  humanFactorsOf,
  h,
  healthOf,
  objectRef,
  outcomeOf,
  predictionId,
  probOf,
  probsAt,
  riskOfProb,
  snapTime,
} from './world';
import { DAY, HOUR, MIN, fmtT } from './time';

export const DECISION_LABELS: Record<DecisionType, string> = {
  dispatch: 'Выезд бригады',
  false_alarm: 'Ложное срабатывание',
  monitor: 'Наблюдение',
};
export const STATUS_LABELS: Record<WorkOrderStatus, string> = {
  draft: 'Черновик',
  submitted: 'Отправлена',
  in_progress: 'В работе',
  done: 'Выполнена',
  cancelled: 'Отменена',
};
export const PRIORITY_LABELS: Record<WorkOrderPriority, string> = {
  low: 'Низкий',
  medium: 'Средний',
  high: 'Высокий',
  critical: 'Аварийный',
};
export const TRANSITIONS: Record<WorkOrderStatus, WorkOrderStatus[]> = {
  draft: ['submitted', 'cancelled'],
  submitted: ['in_progress', 'draft', 'cancelled'],
  in_progress: ['done', 'cancelled'],
  done: [],
  cancelled: [],
};
export const OPEN_STATUSES: WorkOrderStatus[] = ['draft', 'submitted', 'in_progress'];
export const PRIORITY_BY_RISK = { critical: 'critical', risk: 'high', attention: 'medium', normal: 'low' } as const;
export const DUE_HOURS: Record<WorkOrderPriority, number> = { critical: 4, high: 24, medium: 72, low: 168 };

const userRef = (u: (typeof USERS)[number]) => ({ id: u.id, username: u.username, full_name: u.full_name });

export const channelRef = (ch: MockChannel): ChannelRef => ({
  id: ch.id,
  name: ch.name,
  sensor_type: ch.kind.sensor,
  system_type: ch.kind.system,
});

// ---------- решения ----------

// ---------- сохранение изменений моков в localStorage (переживают перезагрузку страницы) ----------

const LS_KEY = 'collector.mock-state.v1';
type StoredDecision = DecisionOut & { _at: number };
interface Persisted {
  decisions: [number, StoredDecision[]][];
  workOrders: StoredWO[];
}

function loadState(): Persisted {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) return JSON.parse(raw) as Persisted;
  } catch {
    /* нет доступа к хранилищу — начинаем с чистого листа */
  }
  return { decisions: [], workOrders: [] };
}

const persisted = loadState();
const changedWorkOrders = new Map<number, StoredWO>(persisted.workOrders.map((w) => [w.id, w]));

function saveState() {
  try {
    const state: Persisted = { decisions: [...userDecisions.entries()], workOrders: [...changedWorkOrders.values()] };
    localStorage.setItem(LS_KEY, JSON.stringify(state));
  } catch {
    /* не сохранили — изменения живут до перезагрузки */
  }
}

const userDecisions = new Map<number, StoredDecision[]>(persisted.decisions);
let nextDecisionId = Math.max(90_000_000, ...persisted.decisions.flatMap(([, l]) => l.map((d) => d.id + 1)));

/** Решения, «принятые до нас» — детерминированно для части тревожных прогнозов */
function seededDecision(pid: number): (DecisionOut & { _at: number }) | null {
  const d = decodePrediction(pid);
  if (!d || d.s >= SNAPSHOTS - 2) return null;
  const p = probOf(d.s, d.idx);
  const level = riskOfProb(p);
  if (level !== 'risk' && level !== 'critical') return null;
  const u = h(pid, 7);
  if (u >= 0.45) return null;
  const happened = outcomeOf(d.s, d.idx)?.happened ?? false;
  const v = h(pid, 8);
  const type: DecisionType = happened
    ? v < 0.7
      ? 'dispatch'
      : 'monitor'
    : v < 0.6
      ? 'false_alarm'
      : v < 0.85
        ? 'monitor'
        : 'dispatch';
  const reasons = REASONS.filter((r) => r.decision_type === type);
  const reason = reasons[Math.floor(h(pid, 9) * reasons.length)];
  const at = snapTime(d.s) + Math.floor(10 + h(pid, 10) * 110) * MIN;
  const user = USERS[h(pid, 11) < 0.8 ? 0 : 1];
  return {
    id: pid,
    decision_type: type,
    decision_label: DECISION_LABELS[type],
    reason: { id: reason.id, name: reason.name },
    comment: h(pid, 12) < 0.3 ? 'Согласовано со старшим смены' : null,
    user: userRef(user),
    created_at: fmtT(at),
    source: 'user' as const,
    _at: at,
  };
}

export function decisionsFor(pid: number, at: number): DecisionOut[] {
  const all = [seededDecision(pid), ...(userDecisions.get(pid) ?? [])].filter(
    (x): x is DecisionOut & { _at: number } => !!x && x._at <= at,
  );
  return all.map(({ _at, ...d }) => {
    void _at;
    return d;
  });
}

export const lastDecision = (pid: number, at: number): DecisionOut | null => {
  const list = decisionsFor(pid, at);
  return list.length ? list[list.length - 1] : null;
};

export function addDecision(
  pid: number,
  type: DecisionType,
  reasonId: number,
  comment: string | null,
  userId: number,
  at: number,
): DecisionOut {
  const d = decodePrediction(pid)!;
  const created = Math.max(at, snapTime(d.s));
  const reason = REASONS.find((r) => r.id === reasonId)!;
  const user = USERS.find((u) => u.id === userId)!;
  const dec = {
    id: nextDecisionId++,
    decision_type: type,
    decision_label: DECISION_LABELS[type],
    reason: { id: reason.id, name: reason.name },
    comment,
    user: userRef(user),
    created_at: fmtT(created),
    source: 'user' as const,
    _at: created,
  };
  userDecisions.set(pid, [...(userDecisions.get(pid) ?? []), dec]);
  saveState();
  const { _at, ...out } = dec;
  void _at;
  return out;
}

/** Прогнозы, по которым пользователи в этой сессии приняли решения (для журнала) */
export const userDecidedPredictions = () => [...userDecisions.keys()];

// ---------- заявки ----------

type StoredWO = WorkOrderOut & { _created: number; _closed: number | null };
export const workOrders: StoredWO[] = [];
const woByPrediction = new Map<number, number>();
let seeded = false;

export function workOrderNumber(id: number, created: number) {
  return `ЗН-${new Date(created).getUTCFullYear()}-${String(id).padStart(6, '0')}`;
}

export function buildWorkOrder(args: {
  id: number;
  status: WorkOrderStatus;
  priority: WorkOrderPriority;
  pid: number | null;
  ch: MockChannel;
  reasonId: number | null;
  recommendationId: number | null;
  recommendationText: string | null;
  description: string | null;
  assignee: string | null;
  due: number | null;
  userId: number;
  created: number;
  closed: number | null;
}): StoredWO {
  const reason = args.reasonId ? REASONS.find((r) => r.id === args.reasonId) : undefined;
  const rec = args.recommendationId ? RECOMMENDATIONS.find((r) => r.id === args.recommendationId) : undefined;
  const user = USERS.find((u) => u.id === args.userId)!;
  return {
    id: args.id,
    number: workOrderNumber(args.id, args.created),
    status: args.status,
    status_label: STATUS_LABELS[args.status],
    priority: args.priority,
    priority_label: PRIORITY_LABELS[args.priority],
    prediction_id: args.pid,
    channel: channelRef(args.ch),
    object: objectRef(args.ch.objectId),
    reason: reason ? { id: reason.id, name: reason.name } : null,
    recommendation: rec ? { id: rec.id, text: rec.text } : null,
    recommendation_text: args.recommendationText ?? rec?.text ?? null,
    description: args.description,
    assignee: args.assignee,
    due_at: args.due != null ? fmtT(args.due) : null,
    created_by: userRef(user),
    created_at: fmtT(args.created),
    updated_at: fmtT(args.closed ?? args.created),
    closed_at: args.closed != null ? fmtT(args.closed) : null,
    _created: args.created,
    _closed: args.closed,
  };
}

export const recommendationFor = (sensorType: string) =>
  RECOMMENDATIONS.find((r) => r.sensor_type === sensorType) ?? RECOMMENDATIONS.find((r) => r.sensor_type === null)!;

export function describe(ch: MockChannel, pid: number | null): string {
  if (pid == null) return `Заявка по датчику «${ch.name}».`;
  const d = decodePrediction(pid)!;
  const f = humanFactorsOf(d.s, d.idx).map((x) => `— ${x.text}`);
  return [`Черновик из прогноза отказа на 24 ч по датчику «${ch.name}».`, 'Причины:', ...f].join('\n');
}

export function ensureWorkOrders() {
  if (seeded) return;
  seeded = true;
  let id = 1;
  for (let day = 0; day < SNAPSHOTS / 4; day++) {
    const s = day * 4 + 1; // срез 06:00
    const probs = probsAt(s);
    const cand: number[] = [];
    for (let i = 0; i < probs.length; i++) if (probs[i] >= 0.8) cand.push(i);
    cand.sort((a, b) => probs[b] - probs[a]);
    let taken = 0;
    for (const idx of cand) {
      const pid = predictionId(s, idx);
      const dec = seededDecision(pid);
      if (!dec || dec.decision_type !== 'dispatch') continue;
      const ch = CHANNELS[idx];
      const created = dec._at + 20 * MIN;
      const priority = PRIORITY_BY_RISK[riskOfProb(probs[idx])];
      const age = (DATA_END - created) / DAY;
      const v = h(pid, 21);
      let status: WorkOrderStatus;
      if (age > 10) status = v < 0.85 ? 'done' : 'cancelled';
      else if (age > 3) status = v < 0.5 ? 'done' : v < 0.8 ? 'in_progress' : 'submitted';
      else status = v < 0.4 ? 'draft' : v < 0.7 ? 'submitted' : 'in_progress';
      const closed = status === 'done' || status === 'cancelled' ? created + (6 + h(pid, 22) * 60) * HOUR : null;
      const rec = recommendationFor(ch.kind.sensor);
      workOrders.push(
        buildWorkOrder({
          id,
          status,
          priority,
          pid,
          ch,
          reasonId: dec.reason.id,
          recommendationId: rec.id,
          recommendationText: null,
          description: describe(ch, pid),
          assignee: h(pid, 23) < 0.6 ? ['Бригада №1', 'Бригада №2', 'Бригада КИПиА'][Math.floor(h(pid, 24) * 3)] : null,
          due: created + DUE_HOURS[priority] * HOUR,
          userId: dec.user!.id,
          created,
          closed,
        }),
      );
      woByPrediction.set(pid, id);
      id++;
      if (++taken >= (h(day, 25) < 0.3 ? 2 : 1)) break;
    }
  }
  // Изменения из прошлых сессий поверх детерминированного набора
  for (const w of changedWorkOrders.values()) {
    const i = workOrders.findIndex((x) => x.id === w.id);
    if (i >= 0) workOrders[i] = w;
    else workOrders.push(w);
    if (w.prediction_id != null) woByPrediction.set(w.prediction_id, w.id);
  }
}

export function addWorkOrder(wo: StoredWO) {
  workOrders.push(wo);
  if (wo.prediction_id != null) woByPrediction.set(wo.prediction_id, wo.id);
  changedWorkOrders.set(wo.id, wo);
  saveState();
}

export function replaceWorkOrder(wo: StoredWO) {
  const i = workOrders.findIndex((x) => x.id === wo.id);
  if (i >= 0) workOrders[i] = wo;
  changedWorkOrders.set(wo.id, wo);
  saveState();
}

export const nextWorkOrderId = () => {
  ensureWorkOrders();
  return workOrders.reduce((m, w) => Math.max(m, w.id), 0) + 1;
};

export function workOrderForPrediction(pid: number, at: number): StoredWO | null {
  ensureWorkOrders();
  const id = woByPrediction.get(pid);
  const wo = id != null ? workOrders.find((w) => w.id === id) : undefined;
  return wo && wo._created <= at ? wo : null;
}

export const isOpenAt = (w: StoredWO, at: number) =>
  w._created <= at && (OPEN_STATUSES.includes(w.status) || (w._closed != null && w._closed > at));

export const stripWO = (w: StoredWO): WorkOrderOut => {
  const { _created, _closed, ...out } = w;
  void _created;
  void _closed;
  return out;
};

export const shortWO = (w: StoredWO): WorkOrderShort => ({
  id: w.id,
  number: w.number,
  status: w.status,
  priority: w.priority,
  due_at: w.due_at,
});

// ---------- прогнозы в форме API ----------

export function predictionItem(s: number, idx: number, at: number): PredictionItem {
  const ch = CHANNELS[idx];
  const pid = predictionId(s, idx);
  const prob = probOf(s, idx);
  const health = healthOf(prob);
  return {
    prediction_id: pid,
    channel: channelRef(ch),
    object: objectRef(ch.objectId),
    prob,
    health,
    risk_level: riskOfProb(prob),
    factors: factorsOf(s, idx).map((f) => f.phrase),
    factors_human: humanFactorsOf(s, idx),
    outcome: outcomeOf(s, idx),
    decision: lastDecision(pid, at),
    work_order_id: workOrderForPrediction(pid, at)?.id ?? null,
  };
}

export function predictionDetail(s: number, idx: number, at: number): PredictionDetail {
  const pid = predictionId(s, idx);
  return {
    ...predictionItem(s, idx, at),
    at: fmtT(snapTime(s)),
    horizon_h: 24,
    model_version: MODEL_VERSION,
    factors_detail: factorsOf(s, idx),
    decisions: decisionsFor(pid, at),
  };
}

export function channelListItem(ch: MockChannel, s: number): ChannelListItem {
  const prob = s >= 0 ? probOf(s, ch.idx) : null;
  return {
    id: ch.id,
    name: ch.name,
    sensor_type: ch.kind.sensor,
    system_type: ch.kind.system,
    tag: ch.tag,
    object: objectRef(ch.objectId),
    prediction:
      prob == null
        ? null
        : { prediction_id: predictionId(s, ch.idx), prob, health: healthOf(prob), risk_level: riskOfProb(prob) },
  };
}
