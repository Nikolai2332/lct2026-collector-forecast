/**
 * MSW-обработчики всех эндпоинтов, которые использует интерфейс. Формы ответов — по backend/openapi.json.
 * Учитываются `at` («машина времени»), пагинация limit/offset, фильтры, сортировка и роли.
 */
import { delay, http, HttpResponse, type HttpHandler } from 'msw';
import type {
  Permission,
  ChannelHistory,
  DashboardSummary,
  DecisionIn,
  DecisionType,
  Dictionaries,
  HistoryPoint,
  JournalItem,
  MaintenanceAdvice,
  ModelMetrics,
  ObjectDetail,
  ObjectNode,
  RiskLevel,
  WorkOrderIn,
  WorkOrderPatch,
  WorkOrderPriority,
  WorkOrderStatus,
} from '@/types';
import { mockAdvice, ADVICE_SOURCE } from './advice';
import { accuracy30d, dailyFacts, modelMetrics, thresholdTable } from './analytics';
import {
  DECISION_LABELS,
  DUE_HOURS,
  OPEN_STATUSES,
  PRIORITY_BY_RISK,
  PRIORITY_LABELS,
  STATUS_LABELS,
  TRANSITIONS,
  addDecision,
  addWorkOrder,
  buildWorkOrder,
  channelListItem,
  describe,
  ensureWorkOrders,
  isOpenAt,
  lastDecision,
  nextWorkOrderId,
  predictionDetail,
  predictionItem,
  recommendationFor,
  replaceWorkOrder,
  shortWO,
  stripWO,
  userDecidedPredictions,
  workOrderForPrediction,
  workOrders,
} from './db';
import { broadcast, criticalAt, notificationOf, transitions } from './stream';
import { DAY, HOUR, dayStart, fmtD, fmtT, nowMsk, parseT } from './time';
import { ROLE_LABELS } from '@/utils/labels';
import {
  CHANNELS,
  CHANNEL_BY_ID,
  DATA_END,
  DATA_START,
  MODEL_VERSION,
  OBJECTS,
  REASONS,
  RECOMMENDATIONS,
  SENSOR_KINDS,
  STEP,
  SYSTEM_TYPES,
  USERS,
  type ChannelFilter,
  type MockChannel,
  decodePrediction,
  h,
  healthOf,
  matchChannel,
  objectNodes,
  objectPath,
  outcomeOf,
  predictionId,
  probOf,
  probsAt,
  riskOfProb,
  snapIndexAt,
  snapTime,
  subtreeIds,
} from './world';

// ---------- общие помощники ----------

const ACT_ROLES = ['dispatcher', 'engineer', 'admin'];
const TOKEN_PREFIX = 'mock-token.';

const err = (status: number, detail: string) => HttpResponse.json({ detail }, { status });
const validation = (loc: string[], msg: string) =>
  HttpResponse.json({ detail: [{ loc, msg, type: 'value_error' }] }, { status: 422 });

type User = (typeof USERS)[number];

function userOf(request: Request): User | null {
  const auth = request.headers.get('Authorization') ?? '';
  const token = auth.startsWith('Bearer ') ? auth.slice(7) : new URL(request.url).searchParams.get('token') ?? '';
  if (!token.startsWith(TOKEN_PREFIX)) return null;
  return USERS.find((u) => u.username === token.slice(TOKEN_PREFIX.length)) ?? null;
}

/** Права ролей — как app/access.py (PERMISSIONS) */
const PERMS: Record<User['role'], Permission[]> = {
  dispatcher_ods: ['view', 'export', 'decide', 'work_orders', 'work_order_status', 'sim_control'],
  dispatcher: ['view', 'export', 'decide', 'work_orders', 'work_order_status'],
  technician: ['view', 'export', 'work_order_status'],
  engineer: ['view', 'export', 'decide', 'work_orders', 'work_order_status', 'data_import', 'sim_control'],
  manager: ['view', 'export'],
  admin: ['view', 'export', 'decide', 'work_orders', 'work_order_status', 'data_import', 'sim_control', 'settings', 'users_admin', 'delete_any_draft'],
};

const userOut = (u: User) => ({
  id: u.id,
  username: u.username,
  full_name: u.full_name,
  role: u.role,
  role_label: ROLE_LABELS[u.role],
  roles: [u.role],
  scope: [],
  unrestricted: true,
  scope_label: 'все объекты (моки без областей)',
  permissions: PERMS[u.role],
  auth_source: 'local' as const,
  is_active: true,
});

interface Ctx {
  url: URL;
  p: URLSearchParams;
  user: User;
  /** Момент `at` в мс; без параметра — текущее время МСК */
  at: number;
  atStr: string;
  request: Request;
  params: Record<string, string | readonly string[] | undefined>;
}

type Handler = (ctx: Ctx) => Response | Promise<Response>;

/** Проверка токена, роли и разбор `at`; небольшая задержка, чтобы были видны скелетоны */
function route(fn: Handler, roles?: string[]) {
  return async ({ request, params }: { request: Request; params: Record<string, string | readonly string[] | undefined> }) => {
    await delay(120 + Math.floor(Math.random() * 180));
    const user = userOf(request);
    if (!user) return err(401, 'Требуется вход в систему');
    if (roles && !roles.includes(user.role)) return err(403, 'Недостаточно прав для этого действия');
    const url = new URL(request.url);
    const p = url.searchParams;
    const raw = p.get('at');
    let at: number;
    if (raw) {
      at = parseT(raw);
      if (Number.isNaN(at)) return validation(['query', 'at'], 'Неверный формат даты и времени');
    } else at = nowMsk();
    return fn({ url, p, user, at, atStr: fmtT(at), request, params });
  };
}

const int = (p: URLSearchParams, key: string, def: number, min = 0, max = Number.MAX_SAFE_INTEGER) => {
  const v = p.get(key);
  const n = v == null || v === '' ? def : Number(v);
  return Number.isFinite(n) ? Math.min(max, Math.max(min, Math.floor(n))) : def;
};
const page = (p: URLSearchParams) => ({ limit: int(p, 'limit', 50, 1, 500), offset: int(p, 'offset', 0, 0) });
const optInt = (p: URLSearchParams, key: string) => (p.get(key) ? Number(p.get(key)) : null);

function channelFilter(p: URLSearchParams, objectId?: number | null): ChannelFilter {
  const oid = objectId ?? optInt(p, 'object_id');
  return {
    objectIds: oid != null ? subtreeIds(oid) : null,
    system_type: p.get('system_type'),
    sensor_type: p.get('sensor_type'),
    q: p.get('q'),
  };
}

function channelList(p: URLSearchParams, s: number, at: string, objectId?: number) {
  const f = channelFilter(p, objectId);
  const levels = p.getAll('risk_level') as RiskLevel[];
  const probs = s >= 0 ? probsAt(s) : null;
  let list = CHANNELS.filter((ch) => matchChannel(ch, f));
  if (levels.length) list = list.filter((ch) => probs && levels.includes(riskOfProb(probs[ch.idx])));
  if ((p.get('sort') ?? 'risk') === 'name') list.sort((a, b) => a.name.localeCompare(b.name, 'ru'));
  else if (probs) list.sort((a, b) => probs[b.idx] - probs[a.idx] || a.id - b.id);
  const { limit, offset } = page(p);
  return { at, total: list.length, items: list.slice(offset, offset + limit).map((ch) => channelListItem(ch, s)) };
}

// ---------- история датчика ----------

function historyPoints(ch: MockChannel, from: number, to: number, stepMs: number): HistoryPoint[] {
  const pts: HistoryPoint[] = [];
  const k = ch.kind;
  const perDay = stepMs / DAY;
  for (let t = from; t < to; t += stepMs) {
    if (t < DATA_START || t >= DATA_END) continue;
    const slot = Math.floor(t / stepMs);
    const u = h(ch.id, slot);
    const faults = ch.faults.filter((f) => f >= t && f < t + stepMs).length;
    // Перед отказом — рост неисправностей, «Неопределен», смен статуса
    const nextF = ch.faults.find((f) => f >= t);
    const near = nextF != null && nextF - t < 2 * DAY ? 1 - (nextF - t) / (2 * DAY) : 0;
    const scale = (x: number) => Math.round(x * perDay);
    const point: HistoryPoint = {
      t: fmtT(t),
      events_count: k.numeric ? Math.max(1, scale(12)) : scale(2 + u * 3) + Math.round(near * 30 * perDay * (0.5 + u)),
      alarm_count: faults + (near > 0.6 && u < 0.5 ? 1 : 0),
      fault_count: faults + Math.round(near * near * 6 * Math.max(perDay, 0.2) * u),
      uncertain_count: Math.round(near * 5 * Math.max(perDay, 0.2) * h(ch.id, slot, 1)),
      status_changes: scale(2) + Math.round(near * 38 * Math.max(perDay, 0.1) * h(ch.id, slot, 2)),
      value_avg: null,
      value_min: null,
      value_max: null,
    };
    if (k.numeric) {
      const spread = k.spread ?? 0;
      const drift = spread * 3 * near;
      const avg = Math.max(0, ch.base + (h(ch.id, slot, 3) - 0.5) * spread * 0.6 + drift + Math.sin(t / DAY) * spread * 0.2);
      const r3 = (x: number) => Math.round(x * 1000) / 1000;
      point.value_avg = r3(avg);
      point.value_min = r3(Math.max(0, avg - spread * (0.2 + 0.3 * perDay)));
      point.value_max = r3(avg + spread * (0.2 + 0.3 * perDay) + drift * 0.5);
    }
    pts.push(point);
  }
  return pts;
}

// ---------- журнал ----------

function journalPids(p: URLSearchParams, at: number): number[] {
  const df = p.get('date_from');
  const dt = p.get('date_to');
  const to = Math.min(at, dt ? parseT(dt) + DAY - 1000 : at);
  const from = df ? parseT(df) : at - 7 * DAY;
  const onlyAlerts = (p.get('only_alerts') ?? 'true') !== 'false';
  const levels = p.getAll('risk_level') as RiskLevel[];
  const decision = p.get('decision');
  const outcome = p.get('outcome');
  const f = channelFilter(p);
  const mask = CHANNELS.filter((ch) => matchChannel(ch, f)).map((ch) => ch.idx);
  const decided = new Set(userDecidedPredictions());
  const out: number[] = [];
  const sHi = snapIndexAt(to);
  const sLo = Math.max(0, Math.ceil((from - DATA_START) / STEP));
  for (let s = sHi; s >= sLo; s--) {
    const probs = probsAt(s);
    const rows: number[] = [];
    for (const idx of mask) {
      const prob = probs[idx];
      const level = riskOfProb(prob);
      const pid = predictionId(s, idx);
      const alert = level === 'risk' || level === 'critical';
      if (onlyAlerts && !alert && !decided.has(pid)) continue;
      if (levels.length && !levels.includes(level)) continue;
      if (decision || (onlyAlerts && !alert)) {
        const d = lastDecision(pid, at);
        if (onlyAlerts && !alert && !d) continue;
        if (decision === 'none' ? d : decision && d?.decision_type !== decision) continue;
      }
      if (outcome) {
        const o = outcomeOf(s, idx);
        const code = o == null ? 'unknown' : o.happened ? 'happened' : 'not_happened';
        if (code !== outcome) continue;
      }
      rows.push(idx);
    }
    rows.sort((a, b) => probs[b] - probs[a]);
    rows.forEach((idx) => out.push(predictionId(s, idx)));
  }
  return out;
}

function objectRefOf(oid: number) {
  const o = OBJECTS.get(oid);
  return { id: oid, name: o?.name ?? '—', path: objectPath(oid) };
}

function planItem(pid: number, ch: MockChannel, advice: MaintenanceAdvice, s: number) {
  const prob = probOf(s, ch.idx);
  return {
    prediction_id: pid,
    channel: { id: ch.id, name: ch.name, sensor_type: ch.kind.sensor, system_type: ch.kind.system },
    prob,
    risk_level: riskOfProb(prob),
    advice,
  };
}

function journalItem(pid: number, at: number): JournalItem {
  const d = decodePrediction(pid)!;
  const item = predictionItem(d.s, d.idx, at);
  const wo = workOrderForPrediction(pid, at);
  const a = mockAdvice(pid, at)!;
  return {
    ...item,
    at: fmtT(snapTime(d.s)),
    work_order_status: wo?.status ?? null,
    work_order_number: wo?.number ?? null,
    recommendation: { rule_id: a.rule_id, title: a.title, action: a.action, priority: a.priority, fault_kind_label: a.fault_kind_label },
  };
}

// ---------- настройки (в моках уровни риска не пересчитываются) ----------

const SETTINGS_DEFAULTS = { risk_attention: 0.2, risk_risk: 0.5, risk_critical: 0.8, notify_cooldown_hours: 24, notify_per_slice: 3, notify_sim_per_slice: 5 };
const mockSettings = { ...SETTINGS_DEFAULTS, custom_risk_bounds: false, defaults: SETTINGS_DEFAULTS, updated_at: null as string | null, updated_by: null as string | null };

// ---------- обработчики ----------

export const handlers: HttpHandler[] = [
  http.get('/api/settings', route(() => HttpResponse.json(mockSettings))),
  http.put(
    '/api/settings',
    route(async ({ request, user }) => {
      const body = (await request.json().catch(() => ({}))) as typeof SETTINGS_DEFAULTS;
      if (!(body.risk_attention < body.risk_risk && body.risk_risk < body.risk_critical))
        return validation(['body'], 'Границы должны возрастать: «внимание» < «риск» < «критично»');
      Object.assign(mockSettings, body, { custom_risk_bounds: true, updated_at: fmtT(nowMsk()), updated_by: user.username });
      return HttpResponse.json(mockSettings);
    }, ['admin']),
  ),

  http.get('/api/health', () =>
    HttpResponse.json({ status: 'ok', version: '0.1.0-mock', model_version: MODEL_VERSION, database: 'ok', time: fmtT(nowMsk()), demo: true }),
  ),

  // Симуляция потока работает только с реальным API (модельные часы — на сервере)
  http.get('/api/sim/state', () =>
    HttpResponse.json({
      enabled: false, active: false, day: null, speed: 60, model_time: null, snapshot_at: null, started_at: null,
      started_by: null, stopped_at: null, stop_reason: null, events_received: 0, events_accepted: 0, critical_sent: 0,
      available_from: null, available_to: null, default_day: null,
      note: 'Воспроизведение заранее рассчитанных прогнозов; онлайн-пересчёт моделью не выполняется.',
    }),
  ),

  http.post('/api/auth/login', async ({ request }) => {
    await delay(250);
    const body = (await request.json().catch(() => ({}))) as { username?: string; password?: string };
    const u = USERS.find((x) => x.username === body.username && x.password === body.password);
    if (!u) return err(401, 'Неверный логин или пароль');
    return HttpResponse.json({ access_token: TOKEN_PREFIX + u.username, token_type: 'bearer', expires_in: 43200, user: userOut(u) });
  }),

  http.get('/api/auth/me', route(({ user }) => HttpResponse.json(userOut(user)))),

  http.get(
    '/api/dictionaries',
    route(() => {
      const counts = new Map<string, number>();
      CHANNELS.forEach((c) => counts.set(c.kind.sensor, (counts.get(c.kind.sensor) ?? 0) + 1));
      const body: Dictionaries = {
        risk_levels: [
          { code: 'normal', name: 'Норма', color: 'green' },
          { code: 'attention', name: 'Внимание', color: 'yellow' },
          { code: 'risk', name: 'Риск', color: 'orange' },
          { code: 'critical', name: 'Критично', color: 'red' },
        ],
        decision_types: Object.entries(DECISION_LABELS).map(([code, name]) => ({ code, name })),
        work_order_statuses: Object.entries(STATUS_LABELS).map(([code, name]) => ({ code, name })),
        work_order_priorities: Object.entries(PRIORITY_LABELS).map(([code, name]) => ({ code, name })),
        roles: Object.entries(ROLE_LABELS).map(([code, name]) => ({ code, name })),
        system_types: SYSTEM_TYPES,
        sensor_types: SENSOR_KINDS.map((k) => ({ name: k.sensor, system_type: k.system, channels_count: counts.get(k.sensor) ?? 0 })),
      };
      return HttpResponse.json(body);
    }),
  ),

  http.get(
    '/api/reasons',
    route(({ p }) => {
      const t = p.get('decision_type');
      return HttpResponse.json(t ? REASONS.filter((r) => r.decision_type === t || r.decision_type === null) : REASONS);
    }),
  ),

  http.get(
    '/api/recommendations',
    route(({ p }) => {
      const st = p.get('sensor_type');
      const rank = (r: (typeof RECOMMENDATIONS)[number]) => (st && r.sensor_type === st ? 0 : r.sensor_type === null ? 1 : 2);
      return HttpResponse.json([...RECOMMENDATIONS].sort((a, b) => rank(a) - rank(b) || a.id - b.id));
    }),
  ),

  http.get(
    '/api/dashboard/summary',
    route(({ at, atStr }) => {
      ensureWorkOrders();
      const s = snapIndexAt(at);
      const { selected } = thresholdTable();
      const dist: Record<string, number> = { normal: 0, attention: 0, risk: 0, critical: 0 };
      let atRisk = 0;
      let predicted = 0;
      let expected = 0;
      if (s >= 0) {
        const probs = probsAt(s);
        for (let i = 0; i < probs.length; i++) {
          const level = riskOfProb(probs[i]);
          dist[level]++;
          if (level === 'risk' || level === 'critical') atRisk++;
          if (probs[i] >= selected) predicted++;
          expected += probs[i];
        }
      }
      const acc = accuracy30d(at, selected);
      const body: DashboardSummary = {
        at: atStr,
        snapshot_at: s >= 0 ? fmtT(snapTime(s)) : null,
        model_version: MODEL_VERSION,
        channels_total: CHANNELS.length,
        channels_at_risk: atRisk,
        predicted_failures_24h: predicted,
        expected_failures_24h: Math.round(expected * 10) / 10,
        open_work_orders: workOrders.filter((w) => isOpenAt(w, at)).length,
        accuracy_30d: { ...acc, window_from: fmtT(acc.window_from), window_to: fmtT(acc.window_to) },
        risk_distribution: dist,
      };
      return HttpResponse.json(body);
    }),
  ),

  http.get(
    '/api/objects',
    route(({ at, atStr, p }) => {
      const s = snapIndexAt(at);
      const nodes = objectNodes(s, channelFilter(p));
      for (const n of nodes.values()) if (n.parent_id != null) nodes.get(n.parent_id)!.children!.push(n);
      return HttpResponse.json({ at: atStr, snapshot_at: s >= 0 ? fmtT(snapTime(s)) : null, items: [nodes.get(1)!] });
    }),
  ),

  http.get(
    '/api/objects/:id',
    route(({ at, atStr, p, params }) => {
      const id = Number(params.id);
      const o = OBJECTS.get(id);
      if (!o) return err(404, 'Объект не найден');
      const s = snapIndexAt(at);
      const nodes = objectNodes(s, channelFilter(p, null));
      const strip = (n: ObjectNode): ObjectNode => ({ ...n, children: [] });
      const body: ObjectDetail = {
        at: atStr,
        object: strip(nodes.get(id)!),
        path: objectPath(id),
        children: o.children.map((c) => strip(nodes.get(c)!)),
        channels: channelList(p, s, atStr, id),
      };
      return HttpResponse.json(body);
    }),
  ),

  http.get('/api/channels', route(({ at, atStr, p }) => HttpResponse.json(channelList(p, snapIndexAt(at), atStr)))),

  http.get(
    '/api/channels/:id',
    route(({ at, atStr, params }) => {
      const ch = CHANNEL_BY_ID.get(Number(params.id));
      if (!ch) return err(404, 'Датчик не найден');
      ensureWorkOrders();
      const s = snapIndexAt(at);
      const pred = s >= 0 ? predictionDetail(s, ch.idx, at) : null;
      const lastT = Math.min(at, DATA_END - 1) - Math.floor(h(ch.id, Math.floor(at / HOUR)) * 50) * 60_000;
      const near = ch.faults.find((f) => f <= lastT && lastT - f < 3 * HOUR);
      const numVal = ch.kind.numeric ? Math.max(0, ch.base + (h(ch.id, 77) - 0.5) * (ch.kind.spread ?? 0)) : null;
      return HttpResponse.json({
        at: atStr,
        id: ch.id,
        name: ch.name,
        sensor_type: ch.kind.sensor,
        system_type: ch.kind.system,
        tag: ch.tag,
        tag_levels: ch.tag.replace(/\.$/, '').split(/[-.]/),
        object: { id: ch.objectId, name: OBJECTS.get(ch.objectId)!.name, path: objectPath(ch.objectId) },
        prediction: pred,
        last_event:
          lastT >= DATA_START
            ? {
                ts: fmtT(lastT),
                is_alarm: !!near,
                value: near ? 'Неисправен' : numVal != null ? String(Math.round(numVal * 1000) / 1000) : 'Норма',
                value_num: near ? null : numVal != null ? Math.round(numVal * 1000) / 1000 : null,
              }
            : null,
        on_watch: pred?.decision?.decision_type === 'monitor',
        open_work_orders: workOrders.filter((w) => w.channel.id === ch.id && isOpenAt(w, at)).map(shortWO),
      });
    }),
  ),

  http.get(
    '/api/channels/:id/history',
    route(({ at, atStr, p, params }) => {
      const ch = CHANNEL_BY_ID.get(Number(params.id));
      if (!ch) return err(404, 'Датчик не найден');
      const gran = p.get('granularity') === 'hour' ? 'hour' : 'day';
      const days = int(p, 'days', 30, 1, gran === 'hour' ? 30 : 90);
      const end = gran === 'day' ? dayStart(at) + DAY : Math.floor(at / HOUR) * HOUR + HOUR;
      const from = end - days * DAY;
      const preds = [];
      for (let s = Math.max(0, snapIndexAt(from)); s >= 0 && s <= snapIndexAt(at); s++) {
        if (snapTime(s) < from) continue;
        const prob = probOf(s, ch.idx);
        preds.push({ prediction_id: predictionId(s, ch.idx), at: fmtT(snapTime(s)), prob, health: healthOf(prob), risk_level: riskOfProb(prob) });
      }
      const body: ChannelHistory = {
        channel_id: ch.id,
        at: atStr,
        date_from: fmtT(from),
        date_to: fmtT(Math.min(end, at)),
        granularity: gran,
        points: historyPoints(ch, from, Math.min(end, at + 1), gran === 'day' ? DAY : HOUR),
        faults: ch.faults
          .map((f, i) => ({ ts: fmtT(f), kind: ch.faultKinds[i], _t: f }))
          .filter((f) => f._t >= from && f._t <= at)
          .map(({ ts, kind }) => ({ ts, kind })),
        predictions: preds,
      };
      return HttpResponse.json(body);
    }),
  ),

  http.get(
    '/api/predictions',
    route(({ at, atStr, p }) => {
      const s = snapIndexAt(at);
      if (s < 0) return HttpResponse.json({ at: atStr, total: 0, items: [] });
      const probs = probsAt(s);
      const levels = p.getAll('risk_level') as RiskLevel[];
      const minProb = p.get('min_prob') ? Number(p.get('min_prob')) : null;
      const f = channelFilter(p);
      const list = CHANNELS.filter(
        (ch) =>
          matchChannel(ch, f) &&
          (!levels.length || levels.includes(riskOfProb(probs[ch.idx]))) &&
          (minProb == null || probs[ch.idx] >= minProb),
      ).sort((a, b) => probs[b.idx] - probs[a.idx] || a.id - b.id);
      const { limit, offset } = page(p);
      return HttpResponse.json({
        at: atStr,
        total: list.length,
        items: list.slice(offset, offset + limit).map((ch) => predictionItem(s, ch.idx, at)),
      });
    }),
  ),

  http.get(
    '/api/predictions/:id',
    route(({ at, params }) => {
      const d = decodePrediction(Number(params.id));
      if (!d) return err(404, 'Прогноз не найден');
      return HttpResponse.json(predictionDetail(d.s, d.idx, at));
    }),
  ),

  http.get(
    '/api/predictions/:id/recommendation',
    route(({ at, params }) => {
      const a = mockAdvice(Number(params.id), at);
      return a ? HttpResponse.json(a) : err(404, 'Прогноз не найден');
    }),
  ),

  http.get(
    '/api/maintenance/plan',
    route(({ at, atStr }) => {
      ensureWorkOrders();
      const s = snapIndexAt(at);
      const groups = new Map<number, ReturnType<typeof planItem>[]>();
      if (s >= 0)
        for (const ch of CHANNELS) {
          const pid = predictionId(s, ch.idx);
          const a = mockAdvice(pid, at);
          if (!a || !a.plan || !a.work_order_needed || workOrderForPrediction(pid, at)) continue;
          const list = groups.get(ch.objectId) ?? [];
          list.push(planItem(pid, ch, a, s));
          groups.set(ch.objectId, list);
        }
      const rank = { low: 0, medium: 1, high: 2, critical: 3 } as const;
      const out = [...groups.entries()]
        .map(([oid, items]) => {
          items.sort((x, y) => rank[y.advice.priority] - rank[x.advice.priority] || y.prob - x.prob);
          return { object: objectRefOf(oid), top_priority: items[0].advice.priority, items };
        })
        .sort((x, y) => rank[y.top_priority] - rank[x.top_priority] || y.items.length - x.items.length);
      return HttpResponse.json({
        at: atStr,
        snapshot_at: s >= 0 ? fmtT(snapTime(s)) : null,
        total_items: out.reduce((n, g) => n + g.items.length, 0),
        total_objects: out.length,
        groups: out.slice(0, 50),
        source: ADVICE_SOURCE,
      });
    }),
  ),

  http.post(
    '/api/maintenance/drafts',
    route(async ({ at, request, user }) => {
      ensureWorkOrders();
      const body = (await request.json().catch(() => ({}))) as { prediction_ids?: number[] };
      if (!body.prediction_ids?.length) return validation(['body', 'prediction_ids'], 'Выберите хотя бы одну работу');
      const created = [];
      const skipped = [];
      for (const pid of body.prediction_ids) {
        const d = decodePrediction(pid);
        const a = mockAdvice(pid, at);
        if (!d || !a) {
          skipped.push({ prediction_id: pid, reason: 'Прогноз не найден' });
          continue;
        }
        if (workOrderForPrediction(pid, at)) {
          skipped.push({ prediction_id: pid, reason: 'По датчику уже открыта заявка' });
          continue;
        }
        const ch = CHANNELS[d.idx];
        const createdAt = Math.max(at, snapTime(d.s));
        const wo = buildWorkOrder({
          id: nextWorkOrderId(), status: 'draft', priority: a.priority, pid, ch, reasonId: null, recommendationId: null,
          recommendationText: a.action, description: `${describe(ch, pid)}
Рекомендация (${a.title}): ${a.reason}`,
          assignee: a.assignee, due: createdAt + a.due_hours * HOUR, userId: user.id, created: createdAt, closed: null,
        });
        addWorkOrder(wo);
        created.push(stripWO(wo));
      }
      return HttpResponse.json({ created, skipped }, { status: 201 });
    }, ACT_ROLES),
  ),

  http.post(
    '/api/predictions/:id/decision',
    route(async ({ at, params, request, user }) => {
      const pid = Number(params.id);
      const d = decodePrediction(pid);
      if (!d) return err(404, 'Прогноз не найден');
      const body = (await request.json().catch(() => ({}))) as Partial<DecisionIn>;
      if (!body.decision_type || !(body.decision_type in DECISION_LABELS))
        return validation(['body', 'decision_type'], 'Выберите решение: dispatch, false_alarm или monitor');
      if (body.reason_id == null) return validation(['body', 'reason_id'], 'Поле обязательно');
      const reason = REASONS.find((r) => r.id === body.reason_id);
      if (!reason) return validation(['body', 'reason_id'], 'Причина не найдена');
      if (reason.decision_type && reason.decision_type !== body.decision_type)
        return validation(['body', 'reason_id'], 'Причина не подходит к выбранному решению');
      addDecision(pid, body.decision_type as DecisionType, reason.id, body.comment?.trim() || null, user.id, at);
      return HttpResponse.json(predictionDetail(d.s, d.idx, Math.max(at, snapTime(d.s))), { status: 201 });
    }, ACT_ROLES),
  ),

  http.get(
    '/api/journal',
    route(({ at, atStr, p }) => {
      ensureWorkOrders();
      const pids = journalPids(p, at);
      const { limit, offset } = page(p);
      return HttpResponse.json({ at: atStr, total: pids.length, items: pids.slice(offset, offset + limit).map((pid) => journalItem(pid, at)) });
    }),
  ),

  http.get(
    '/api/export/journal',
    route(({ at, p }) => {
      ensureWorkOrders();
      // В моках — CSV с теми же колонками; настоящий XLSX отдаёт бэкенд
      const rows = journalPids(p, at)
        .slice(0, 50_000)
        .map((pid) => journalItem(pid, at));
      const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`;
      const head = ['Время', 'Датчик', 'Тип', 'Объект', 'Вероятность', 'Здоровье', 'Уровень', 'Решение', 'Причина', 'Исход', 'Заявка'];
      const lines = rows.map((r) =>
        [
          r.at.replace('T', ' '),
          r.channel.name,
          r.channel.sensor_type,
          r.object.path.join(' / '),
          r.prob.toFixed(2).replace('.', ','),
          r.health,
          r.risk_level,
          r.decision?.decision_label,
          r.decision?.reason.name,
          r.outcome == null ? 'неизвестно' : r.outcome.happened ? 'сбылся' : 'не сбылся',
          r.work_order_id,
        ]
          .map(esc)
          .join(';'),
      );
      const csv = '﻿' + [head.map(esc).join(';'), ...lines].join('\r\n');
      return new HttpResponse(csv, {
        headers: {
          'Content-Type': 'text/csv; charset=utf-8',
          'Content-Disposition': `attachment; filename="journal_${fmtD(at)}_demo.csv"`,
        },
      });
    }),
  ),

  http.get(
    '/api/work-orders',
    route(({ at, atStr, p }) => {
      ensureWorkOrders();
      const statuses = p.getAll('status');
      const priorities = p.getAll('priority');
      const oid = optInt(p, 'object_id');
      const objs = oid != null ? subtreeIds(oid) : null;
      const chId = optInt(p, 'channel_id');
      const pid = optInt(p, 'prediction_id');
      const q = p.get('q')?.trim().toLowerCase();
      const list = workOrders
        .filter(
          (w) =>
            w._created <= at &&
            (!statuses.length || statuses.includes(w.status)) &&
            (!priorities.length || priorities.includes(w.priority)) &&
            (!objs || objs.has(w.object.id)) &&
            (chId == null || w.channel.id === chId) &&
            (pid == null || w.prediction_id === pid) &&
            (!q ||
              [w.number, w.channel.name, w.object.name, w.description ?? '', w.assignee ?? '']
                .join(' ')
                .toLowerCase()
                .includes(q)),
        )
        .sort((a, b) => b._created - a._created || b.id - a.id);
      const { limit, offset } = page(p);
      return HttpResponse.json({ at: atStr, total: list.length, items: list.slice(offset, offset + limit).map(stripWO) });
    }),
  ),

  http.get(
    '/api/work-orders/:id',
    route(({ params }) => {
      ensureWorkOrders();
      const w = workOrders.find((x) => x.id === Number(params.id));
      return w ? HttpResponse.json(stripWO(w)) : err(404, 'Заявка не найдена');
    }),
  ),

  http.post(
    '/api/work-orders',
    route(async ({ at, request, user }) => {
      ensureWorkOrders();
      const body = (await request.json().catch(() => ({}))) as WorkOrderIn;
      let ch: MockChannel | undefined;
      let pid: number | null = null;
      let created = at;
      if (body.prediction_id != null) {
        const d = decodePrediction(body.prediction_id);
        if (!d) return err(404, 'Прогноз не найден');
        pid = body.prediction_id;
        ch = CHANNELS[d.idx];
        created = Math.max(at, snapTime(d.s));
      } else if (body.channel_id != null) {
        ch = CHANNEL_BY_ID.get(body.channel_id);
        if (!ch) return err(404, 'Датчик не найден');
      } else return validation(['body'], 'Укажите prediction_id или channel_id');
      // Как на сервере: по датчику уже открыта заявка — 409 с её номером (фактический статус, без «машины времени»)
      ensureWorkOrders();
      const open = workOrders.find((w) => w.channel.id === ch!.id && OPEN_STATUSES.includes(w.status));
      if (open)
        return HttpResponse.json(
          {
            detail: `По датчику уже есть открытая заявка ${open.number} (${STATUS_LABELS[open.status].toLowerCase()}); откройте её или закройте, прежде чем создавать новую`,
            work_order: { id: open.id, number: open.number, status: open.status, status_label: STATUS_LABELS[open.status] },
          },
          { status: 409 },
        );
      const d = pid != null ? decodePrediction(pid)! : null;
      const priority: WorkOrderPriority =
        body.priority ?? (d ? PRIORITY_BY_RISK[riskOfProb(probOf(d.s, d.idx))] : 'medium');
      const rec = body.recommendation_id
        ? RECOMMENDATIONS.find((r) => r.id === body.recommendation_id)
        : recommendationFor(ch.kind.sensor);
      const id = nextWorkOrderId();
      const wo = buildWorkOrder({
        id,
        status: 'draft',
        priority,
        pid,
        ch,
        reasonId: body.reason_id ?? null,
        recommendationId: rec?.id ?? null,
        recommendationText: body.recommendation_text ?? null,
        description: body.description ?? describe(ch, pid),
        assignee: body.assignee ?? null,
        due: body.due_at ? parseT(body.due_at) : created + DUE_HOURS[priority] * HOUR,
        userId: user.id,
        created,
        closed: null,
      });
      addWorkOrder(wo);
      return HttpResponse.json(stripWO(wo), { status: 201 });
    }, ACT_ROLES),
  ),

  http.patch(
    '/api/work-orders/:id',
    route(async ({ at, params, request }) => {
      ensureWorkOrders();
      const i = workOrders.findIndex((x) => x.id === Number(params.id));
      if (i < 0) return err(404, 'Заявка не найдена');
      const w = workOrders[i];
      if (w.status === 'done' || w.status === 'cancelled') return err(409, 'Закрытую заявку менять нельзя');
      const body = (await request.json().catch(() => ({}))) as WorkOrderPatch;
      let status: WorkOrderStatus = w.status;
      if (body.status && body.status !== w.status) {
        if (!TRANSITIONS[w.status].includes(body.status))
          return err(409, `Недопустимый переход: «${STATUS_LABELS[w.status]}» → «${STATUS_LABELS[body.status]}»`);
        status = body.status;
      }
      const priority = body.priority ?? w.priority;
      const stamp = Math.max(at, w._created);
      const closed = status === 'done' || status === 'cancelled' ? stamp : null;
      const reasonId = body.reason_id !== undefined ? body.reason_id : (w.reason?.id ?? null);
      const recId = body.recommendation_id !== undefined ? body.recommendation_id : (w.recommendation?.id ?? null);
      const next = buildWorkOrder({
        id: w.id,
        status,
        priority,
        pid: w.prediction_id,
        ch: CHANNEL_BY_ID.get(w.channel.id)!,
        reasonId,
        recommendationId: recId,
        recommendationText: body.recommendation_text !== undefined ? body.recommendation_text : w.recommendation_text,
        description: body.description !== undefined ? body.description : w.description,
        assignee: body.assignee !== undefined ? body.assignee : w.assignee,
        due: body.due_at !== undefined ? (body.due_at ? parseT(body.due_at) : null) : w.due_at ? parseT(w.due_at) : null,
        userId: w.created_by?.id ?? 1,
        created: w._created,
        closed,
      });
      next.updated_at = fmtT(stamp);
      replaceWorkOrder(next);
      return HttpResponse.json(stripWO(next));
    }, ACT_ROLES),
  ),

  http.get(
    '/api/model/thresholds',
    route(({ atStr }) => {
      const { rows, selected } = thresholdTable();
      return HttpResponse.json({ at: atStr, model_version: MODEL_VERSION, selected_threshold: selected, items: rows });
    }),
  ),

  http.get(
    '/api/model/metrics',
    route(({ at, atStr, p }) => {
      const to = p.get('date_to') ? parseT(p.get('date_to')!) : dayStart(at);
      const from = p.get('date_from') ? parseT(p.get('date_from')!) : to - 30 * DAY;
      if (from > to) return err(422, 'Начало периода позже конца');
      if ((to - from) / DAY > 92) return err(422, 'Период не может быть длиннее 92 дней');
      const m = modelMetrics();
      const { rows } = thresholdTable();
      const body: ModelMetrics = {
        at: atStr,
        model_version: MODEL_VERSION,
        threshold: m.selected,
        test_period_from: fmtD(m.testFrom),
        test_period_to: fmtD(m.testTo),
        overall: m.overall,
        baseline: m.baseline,
        by_sensor_type: m.bySensor,
        precision_at_k: m.precisionAtK,
        median_lead_time_h: m.medianLead,
        pr_curve: rows,
        daily: dailyFacts(from, to, m.selected),
        date_from: fmtD(from),
        date_to: fmtD(to),
      };
      return HttpResponse.json(body);
    }),
  ),

  http.get(
    '/api/notifications',
    route(({ at, atStr, p }) => {
      const hours = int(p, 'hours', 24, 1, 168);
      const pairs = transitions(at, hours);
      const counts = new Map<number, number>();
      pairs.forEach(([s]) => counts.set(s, (counts.get(s) ?? 0) + 1));
      const { limit, offset } = page(p);
      return HttpResponse.json({
        at: atStr,
        total: pairs.length,
        items: pairs.slice(offset, offset + limit).map(([s, idx]) => notificationOf(s, idx, at)),
        groups: [...counts].map(([s, count]) => ({ snapshot_at: fmtT(snapTime(s)), count })),
      });
    }),
  ),

  http.post(
    '/api/notifications/test',
    route(({ at, atStr, p }) => {
      const s = snapIndexAt(at);
      const items = criticalAt(s)
        .slice(0, int(p, 'limit', 1, 1, 20))
        .map((idx) => notificationOf(s, idx, at));
      broadcast(items);
      return HttpResponse.json({ at: atStr, total: items.length, items });
    }, ACT_ROLES),
  ),
];

