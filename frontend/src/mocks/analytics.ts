/** Метрики модели, таблица порогов и точность — считаются по тем же сгенерированным прогнозам, как в сиде бэкенда. */
import type { DailyFact, MetricValuesLike, ThresholdRowLike } from './analyticsTypes';
import {
  ALL_FAULTS,
  CHANNELS,
  DATA_END,
  DATA_START,
  SENSOR_KINDS,
  SNAPSHOTS,
  STEP,
  faultWithin,
  nextFaultIdx,
  probsAt,
  snapIndexAt,
  snapTime,
} from './world';
import { DAY, HOUR, parseT } from './time';

const TEST_FROM = parseT('2026-08-01');
const TEST_TO = parseT('2026-08-31');
const THRESHOLDS = Array.from({ length: 19 }, (_, i) => Math.round((0.05 + i * 0.05) * 100) / 100);
const TARGET_PRECISION = 0.72;

interface Pair {
  p: number;
  y: boolean;
  kind: number;
  base: boolean;
}

let pairs: Pair[] | null = null;
let days = 0;

/** Срезы «датчик × сутки» в 00:00 тестового периода; без срезов с отказом за последние 72 ч (как в ТЗ ML) */
function testPairs(): Pair[] {
  if (pairs) return pairs;
  pairs = [];
  days = 0;
  for (let t = TEST_FROM; t <= TEST_TO && t + DAY <= DATA_END; t += DAY) {
    days++;
    const s = snapIndexAt(t);
    const probs = probsAt(s);
    for (const ch of CHANNELS) {
      const i = nextFaultIdx(ch, t);
      const last = i > 0 ? ch.faults[i - 1] : null;
      if (last != null && t - last < 72 * HOUR) continue;
      pairs.push({
        p: probs[ch.idx],
        y: faultWithin(ch, t, t + DAY) != null,
        kind: SENSOR_KINDS.indexOf(ch.kind),
        base: last != null && t - last < 14 * DAY,
      });
    }
  }
  return pairs;
}

const safe = (a: number, b: number) => (b > 0 ? a / b : null);
const round = (v: number | null, d = 3) => (v == null ? null : Math.round(v * 10 ** d) / 10 ** d);

function valuesFor(list: Pair[], flag: (x: Pair) => boolean, withAuc: boolean): MetricValuesLike {
  let tp = 0;
  let fp = 0;
  let fn = 0;
  for (const x of list) {
    const f = flag(x);
    if (f && x.y) tp++;
    else if (f) fp++;
    else if (x.y) fn++;
  }
  const precision = safe(tp, tp + fp);
  const recall = safe(tp, tp + fn);
  const f1 = precision != null && recall != null && precision + recall > 0 ? (2 * precision * recall) / (precision + recall) : null;
  return {
    precision: round(precision),
    recall: round(recall),
    f1: round(f1),
    pr_auc: withAuc ? round(prAuc(list)) : null,
    support: tp + fn,
  };
}

function prAuc(list: Pair[]): number | null {
  const sorted = [...list].sort((a, b) => b.p - a.p);
  const pos = sorted.reduce((n, x) => n + (x.y ? 1 : 0), 0);
  if (!pos) return null;
  let tp = 0;
  let auc = 0;
  let prevRecall = 0;
  sorted.forEach((x, i) => {
    if (!x.y) return;
    tp++;
    const recall = tp / pos;
    auc += (recall - prevRecall) * (tp / (i + 1));
    prevRecall = recall;
  });
  return auc;
}

let thresholdCache: { rows: ThresholdRowLike[]; selected: number } | null = null;

export function thresholdTable() {
  if (thresholdCache) return thresholdCache;
  const list = testPairs();
  const rows = THRESHOLDS.map((thr) => {
    let tp = 0;
    let fp = 0;
    let fn = 0;
    for (const x of list) {
      if (x.p >= thr) {
        if (x.y) tp++;
        else fp++;
      } else if (x.y) fn++;
    }
    return {
      threshold: thr,
      precision: round(safe(tp, tp + fp) ?? 0)!,
      recall: round(safe(tp, tp + fn) ?? 0)!,
      alerts_per_day: round((tp + fp) / days, 1)!,
      tp_per_day: round(tp / days, 1)!,
      fp_per_day: round(fp / days, 1)!,
    };
  });
  const ok = rows.filter((r) => r.precision >= TARGET_PRECISION);
  const selected = ok.length ? ok.reduce((a, b) => (b.recall > a.recall ? b : a)).threshold : 0.5;
  thresholdCache = { rows, selected };
  return thresholdCache;
}

let metricsCache: ReturnType<typeof buildMetrics> | null = null;

function buildMetrics() {
  const { selected } = thresholdTable();
  const list = testPairs();
  const overall = valuesFor(list, (x) => x.p >= selected, true);
  const baseline = valuesFor(list, (x) => x.base, false);
  const bySensor = SENSOR_KINDS.map((k, i) => {
    const sub = list.filter((x) => x.kind === i);
    return { sensor_type: k.sensor, ...valuesFor(sub, (x) => x.p >= selected, true) };
  }).sort((a, b) => (b.support ?? 0) - (a.support ?? 0));
  // Precision@K: среднее по суткам тестового периода
  const precisionAtK = [20, 50, 100].map((k) => {
    let sum = 0;
    let n = 0;
    for (let t = TEST_FROM; t <= TEST_TO && t + DAY <= DATA_END; t += DAY) {
      const probs = probsAt(snapIndexAt(t));
      const top = [...probs.keys()].sort((a, b) => probs[b] - probs[a]).slice(0, k);
      sum += top.filter((i) => faultWithin(CHANNELS[i], t, t + DAY) != null).length / k;
      n++;
    }
    return { k, precision: round(sum / n)! };
  });
  // Медианное упреждение: от первого среза с тревогой до отказа
  const leads: number[] = [];
  for (const [f, idx] of ALL_FAULTS) {
    if (f < TEST_FROM || f >= DATA_END) continue;
    for (let s = snapIndexAt(f - DAY) + 1; s <= snapIndexAt(f); s++) {
      if (s < 0 || snapTime(s) >= f) continue;
      if (probsAt(s)[idx] >= selected) {
        leads.push((f - snapTime(s)) / HOUR);
        break;
      }
    }
  }
  leads.sort((a, b) => a - b);
  return {
    overall,
    baseline,
    bySensor,
    precisionAtK,
    medianLead: leads.length ? round(leads[Math.floor(leads.length / 2)], 1) : null,
    selected,
  };
}

export function modelMetrics() {
  if (!metricsCache) metricsCache = buildMetrics();
  return { ...metricsCache, testFrom: TEST_FROM, testTo: TEST_TO };
}

/** «Прогноз против факта» по дням: датчик × сутки, как в backend/app/api/model.py */
export function dailyFacts(from: number, to: number, thr: number): DailyFact[] {
  const out: DailyFact[] = [];
  for (let d = from; d <= to; d += DAY) {
    if (d < DATA_START || d >= DATA_END) continue;
    const s0 = snapIndexAt(d);
    const pred = new Set<number>();
    const tpSet = new Set<number>();
    const fact = new Set<number>();
    for (let s = s0; s < s0 + 4 && s < SNAPSHOTS; s++) {
      const probs = probsAt(s);
      const t = snapTime(s);
      for (let i = 0; i < probs.length; i++) {
        const y = t + DAY <= DATA_END && faultWithin(CHANNELS[i], t, t + DAY) != null;
        if (y) fact.add(i);
        if (probs[i] >= thr) {
          pred.add(i);
          if (y) tpSet.add(i);
        }
      }
    }
    out.push({ date: new Date(d).toISOString().slice(0, 10), predicted: pred.size, actual: fact.size, true_positive: tpSet.size });
  }
  return out;
}

const accCache = new Map<number, { tp: number; fp: number; fn: number }>();

/** Точность за 30 дней: пары «датчик × срез» с at в (at − 30 д, at − 24 ч] */
export function accuracy30d(at: number, thr: number) {
  const to = at - DAY;
  const from = at - 30 * DAY;
  let tp = 0;
  let fp = 0;
  let fn = 0;
  const sTo = Math.min(snapIndexAt(to), snapIndexAt(DATA_END - DAY - 1));
  for (let s = Math.max(0, Math.floor((from - DATA_START) / STEP) + 1); s <= sTo; s++) {
    let c = accCache.get(s * 1000 + Math.round(thr * 100));
    if (!c) {
      c = { tp: 0, fp: 0, fn: 0 };
      const probs = probsAt(s);
      const t = snapTime(s);
      for (let i = 0; i < probs.length; i++) {
        const flagged = probs[i] >= thr;
        const y = faultWithin(CHANNELS[i], t, t + DAY) != null;
        if (flagged && y) c.tp++;
        else if (flagged) c.fp++;
        else if (y) c.fn++;
      }
      accCache.set(s * 1000 + Math.round(thr * 100), c);
    }
    tp += c.tp;
    fp += c.fp;
    fn += c.fn;
  }
  return {
    precision: round(safe(tp, tp + fp)),
    recall: round(safe(tp, tp + fn)),
    true_positive: tp,
    false_positive: fp,
    false_negative: fn,
    threshold: thr,
    window_from: from,
    window_to: to,
  };
}
