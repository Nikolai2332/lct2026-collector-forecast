/** Эмуляция SSE /api/notifications/stream в режиме моков: снимок сразу, затем «новые» критические прогнозы по таймеру. */
import type { NotificationItem } from '@/types';
import { plural } from '@/utils/plural';
import { predictionItem } from './db';
import { nowMsk, parseT } from './time';
import { CHANNELS_COUNT, h, predictionId, probsAt, riskOfProb, snapIndexAt, snapTime } from './world';
import { fmtT } from './time';

export function notificationOf(s: number, idx: number, at: number, createdAt?: number): NotificationItem {
  const item = predictionItem(s, idx, at);
  return {
    id: item.prediction_id,
    kind: 'critical_prediction',
    count: 1,
    created_at: fmtT(createdAt ?? snapTime(s)),
    title: `Критический риск: ${item.channel.name}`,
    message: `${item.object.name} · вероятность отказа ${Math.round(item.prob * 100)}\u00a0%`,
    prediction: item,
  };
}

/** Как на бэкенде: «новый критичный» — критичен в срезе и не был критичным в срезах за 24 ч до него (4 среза) */
export function newCriticalAt(s: number): number[] {
  const before = new Set<number>();
  for (let k = Math.max(0, s - 4); k < s; k++) criticalAt(k).forEach((i) => before.add(i));
  return criticalAt(s).filter((i) => !before.has(i));
}

/** Лента за hours часов до at: новые переходы по срезам, новые срезы первыми */
export function transitions(at: number, hours: number): [number, number][] {
  const pairs: [number, number][] = [];
  for (let s = snapIndexAt(at); s >= 0 && snapTime(s) > at - hours * 3_600_000; s--) newCriticalAt(s).forEach((idx) => pairs.push([s, idx]));
  return pairs;
}

/** По срезу — до трёх уведомлений и сводка «ещё N» (как slice_notifications на бэкенде) */
export function sliceNotifications(pairs: [number, number][], at: number, perSlice = 3): NotificationItem[] {
  const out: NotificationItem[] = [];
  const bySlice = new Map<number, number[]>();
  pairs.forEach(([s, idx]) => bySlice.set(s, [...(bySlice.get(s) ?? []), idx]));
  bySlice.forEach((idxs, s) => {
    idxs.slice(0, perSlice).forEach((idx) => out.push(notificationOf(s, idx, at)));
    const rest = idxs.length - perSlice;
    if (rest > 0) {
      const top = notificationOf(s, idxs[perSlice], at);
      out.push({ ...top, kind: 'critical_summary', count: rest, title: `Ещё ${rest} ${plural(rest, 'датчик перешёл', 'датчика перешли', 'датчиков перешли')} в «Критично»` });
    }
  });
  return out;
}

export function criticalAt(s: number): number[] {
  if (s < 0) return [];
  const probs = probsAt(s);
  const out: number[] = [];
  for (let i = 0; i < CHANNELS_COUNT; i++) if (riskOfProb(probs[i]) === 'critical') out.push(i);
  return out.sort((a, b) => probs[b] - probs[a]);
}

type Listener = (n: NotificationItem) => void;
const listeners = new Set<Listener>();

/** Для POST /api/notifications/test */
export function broadcast(items: NotificationItem[]) {
  items.forEach((n) => listeners.forEach((l) => l(n)));
}

export interface StreamOptions {
  firstDelayMs?: number;
  intervalMs?: number;
}

export function subscribe(
  atParam: string | undefined,
  onSnapshot: (items: NotificationItem[]) => void,
  onEvent: Listener,
  { firstDelayMs = 20_000, intervalMs = 90_000 }: StreamOptions = {},
): () => void {
  const at = atParam ? parseT(atParam) : nowMsk();
  const s = snapIndexAt(at);
  const crit = criticalAt(s);
  const snapTimer = setTimeout(() => onSnapshot(sliceNotifications(transitions(at, 24), at)), 300);
  listeners.add(onEvent);
  let n = 0;
  const emit = () => {
    if (!crit.length) return;
    const idx = crit[Math.floor(h(n++, at / 1000) * crit.length)];
    // Новый критический прогноз «пришёл сейчас»: такой же, как в срезе, но с новым id уведомления
    const note = notificationOf(s, idx, at, atParam ? at : nowMsk());
    onEvent({ ...note, id: predictionId(s, idx) * 10 + (n % 10) });
  };
  let interval: ReturnType<typeof setInterval> | undefined;
  const first = setTimeout(() => {
    emit();
    interval = setInterval(emit, intervalMs);
  }, firstDelayMs);
  return () => {
    clearTimeout(snapTimer);
    clearTimeout(first);
    if (interval) clearInterval(interval);
    listeners.delete(onEvent);
  };
}
