/**
 * Детерминированный демо-мир для моков: справочники из backend/app/seed.py, 11 485 датчиков (объём из ТЗ),
 * отказы и прогнозы каждые 6 ч за 01.07–31.08.2026. Прогнозы не хранятся, а вычисляются по (датчик, срез),
 * поэтому моки быстро отвечают на любой `at` и дают все четыре уровня риска.
 */
import type { FactorDetail, FactorHuman, ObjectNode, RiskLevel } from '@/types';
import { DAY, HOUR, parseT } from './time';

export const SEED = 20260922;
export const MODEL_VERSION = 'lgbm-2026.09-demo';
export const CHANNELS_COUNT = 11485;

export const DATA_START = parseT('2026-07-01T00:00:00');
export const DATA_END = parseT('2026-09-01T00:00:00');
export const STEP = 6 * HOUR;
export const SNAPSHOTS = Math.round((DATA_END - DATA_START) / STEP); // 248, последний — 31.08 18:00

export const EXAMPLE_CHANNEL_ID = 334609;
export const EXAMPLE_AT = parseT('2026-08-01T12:00:00');
export const EXAMPLE_FAULT_AT = parseT('2026-08-01T18:42:10');

// ---------- случайность ----------

export function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Детерминированное «случайное» число [0, 1) по набору целых */
export function h(...xs: number[]): number {
  let a = SEED ^ 0x9e3779b9;
  for (const x of xs) {
    a = Math.imul(a ^ (x | 0), 0x85ebca6b);
    a ^= a >>> 13;
    a = Math.imul(a, 0xc2b2ae35);
    a ^= a >>> 16;
  }
  return (a >>> 0) / 4294967296;
}

// ---------- справочники (как в seed.py) ----------

interface SensorKind {
  system: string;
  sensor: string;
  name: string;
  weight: number;
  faultRate: number;
  numeric?: boolean;
  base?: number;
  spread?: number;
  group: 'status' | 'numeric' | 'unit' | 'power';
}

const K = (
  system: string,
  sensor: string,
  name: string,
  weight: number,
  faultRate: number,
  group: SensorKind['group'] = 'status',
  base = 0,
  spread = 0,
): SensorKind => ({ system, sensor, name, weight, faultRate, group, numeric: group === 'numeric', base, spread });

export const SENSOR_KINDS: SensorKind[] = [
  K('Пожарная охрана', 'Датчик дыма', 'Дым ПК {pk}+{n}', 0.3, 0.006),
  K('Пожарная охрана', 'Тепловой извещатель', 'ИП тепловой ПК {pk}+{n}', 0.03, 0.004),
  K('Пожарная охрана', 'Ручной пожарный извещатель', 'ИПР ПК {pk}', 0.02, 0.002),
  K('Газовый контроль', 'Датчик метана', 'Газ CH4 ПК {pk}+{n}', 0.05, 0.006, 'numeric', 0.01, 0.004),
  K('Газовый контроль', 'Датчик угарного газа', 'Газ CO ПК {pk}+{n}', 0.03, 0.006, 'numeric', 2.0, 0.6),
  K('Газовый контроль', 'Датчик кислорода', 'O2 ПК {pk}+{n}', 0.02, 0.005, 'numeric', 20.8, 0.15),
  K('Газовый контроль', 'Датчик сероводорода', 'H2S ПК {pk}+{n}', 0.02, 0.005, 'numeric', 0.3, 0.1),
  K('Охранная сигнализация', 'Датчик движения', 'Движение ПК {pk}+{n}', 0.07, 0.004),
  K('Охранная сигнализация', 'Дверной контакт', 'Дверь ВШ-{k}', 0.08, 0.004),
  K('Охранная сигнализация', 'Датчик люка', 'Люк ВШ-{k}', 0.03, 0.004),
  K('Охранная сигнализация', 'Датчик вскрытия щита', 'Вскрытие щита ЩР-{k}', 0.02, 0.003),
  K('Водоотведение', 'Насос', 'Насос АНС-{k} №{n}', 0.04, 0.035, 'unit'),
  K('Водоотведение', 'Датчик уровня воды', 'Уровень АНС-{k}', 0.02, 0.008, 'numeric', 0.35, 0.1),
  K('Водоотведение', 'Датчик затопления', 'Затопление ПК {pk}', 0.03, 0.004),
  K('Вентиляция', 'Вентилятор', 'Вентилятор ВШ-{k} №{n}', 0.03, 0.025, 'unit'),
  K('Вентиляция', 'Датчик температуры', 'Т° ПК {pk}+{n}', 0.04, 0.005, 'numeric', 24.0, 2.5),
  K('Электроснабжение', 'Контроль фазы', 'Фаза {phase} щит ЩР-{k}', 0.08, 0.03, 'power'),
  K('Электроснабжение', 'Ввод питания', 'Ввод питания ЩР-{k}', 0.02, 0.01, 'power'),
  K('Электроснабжение', 'Датчик освещения', 'Освещение ПК {pk}', 0.02, 0.004),
];
export const SYSTEM_TYPES = [...new Set(SENSOR_KINDS.map((k) => k.system))];

export const REASONS: { id: number; code: string; name: string; decision_type: 'dispatch' | 'false_alarm' | 'monitor' | null }[] = [
  ['model_risk_confirmed', 'Подтверждён риск отказа по данным модели', 'dispatch'],
  ['repeated_fault_msgs', 'Повторные сообщения о неисправности', 'dispatch'],
  ['link_loss', 'Пропадание связи с датчиком', 'dispatch'],
  ['power_loss', 'Обесточивание фазы или щита', 'dispatch'],
  ['flood_risk', 'Риск затопления', 'dispatch'],
  ['planned_works', 'Проводятся плановые работы', 'false_alarm'],
  ['known_interference', 'Известная помеха или наводка', 'false_alarm'],
  ['already_replaced', 'Датчик уже заменён', 'false_alarm'],
  ['comm_glitch', 'Сбой канала связи, оборудование исправно', 'false_alarm'],
  ['staff_pass', 'Срабатывание при проходе персонала', 'false_alarm'],
  ['need_more_data', 'Недостаточно данных, наблюдаем', 'monitor'],
  ['single_spike', 'Единичный всплеск', 'monitor'],
  ['wait_related_service', 'Ожидаем подтверждения от смежной службы', 'monitor'],
  ['other', 'Другое (см. комментарий)', null],
].map(([code, name, decision_type], i) => ({
  id: i + 1,
  code: code as string,
  name: name as string,
  decision_type: decision_type as 'dispatch' | 'false_alarm' | 'monitor' | null,
}));

export const RECOMMENDATIONS: { id: number; code: string; sensor_type: string | null; text: string }[] = [
  ['smoke_check', 'Датчик дыма', 'Проверить и очистить дымовую камеру извещателя, проверить шлейф и адресный модуль.'],
  ['heat_check', 'Тепловой извещатель', 'Проверить тепловой извещатель тестером, осмотреть шлейф.'],
  ['pump_check', 'Насос', 'Проверить насос АНС: питание, пускатель, поплавковые датчики, отсутствие засора всаса.'],
  ['fan_check', 'Вентилятор', 'Проверить пускатель и привод вентилятора, подшипники и крепление; замерить ток двигателя.'],
  ['phase_check', 'Контроль фазы', 'Проверить автоматы и контакторы в щите, замерить напряжение по фазам.'],
  ['feed_check', 'Ввод питания', 'Проверить вводной автомат и АВР, замерить напряжение на вводе.'],
  ['gas_calibration', 'Датчик метана', 'Проверить калибровку газоанализатора поверочной смесью; при дрейфе заменить сенсор.'],
  ['co_calibration', 'Датчик угарного газа', 'Проверить калибровку датчика CO поверочной смесью; при дрейфе заменить сенсор.'],
  ['temp_check', 'Датчик температуры', 'Сравнить показания с переносным термометром, проверить крепление и кабель датчика.'],
  ['motion_check', 'Датчик движения', 'Проверить извещатель движения: загрязнение линзы, крепление, питание.'],
  ['door_check', 'Дверной контакт', 'Проверить геркон и магнит дверного контакта, регулировку двери.'],
  ['hatch_check', 'Датчик люка', 'Проверить датчик люка и уплотнение крышки.'],
  ['water_check', 'Датчик уровня воды', 'Очистить датчик уровня от ила, проверить поплавок и кабель.'],
  ['flood_check', 'Датчик затопления', 'Проверить датчик затопления, очистить контакты.'],
  ['general_inspection', null, 'Провести осмотр датчика и линии связи, проверить питание контроллера.'],
  ['controller_link', null, 'Проверить канал связи контроллера и коммутационное оборудование.'],
].map(([code, sensor_type, text], i) => ({ id: i + 1, code: code as string, sensor_type, text: text as string }));

export const USERS = [
  { id: 1, username: 'dispatcher', password: 'dispatcher123', full_name: 'Диспетчер смены', role: 'dispatcher' as const },
  { id: 2, username: 'engineer', password: 'engineer123', full_name: 'Инженер КИПиА', role: 'engineer' as const },
  { id: 3, username: 'manager', password: 'manager123', full_name: 'Начальник участка', role: 'manager' as const },
  { id: 4, username: 'admin', password: 'admin123', full_name: 'Администратор системы', role: 'admin' as const },
  // Моки не фильтруют данные по области видимости — это делает только настоящий бэкенд (docs/SECURITY.md)
  { id: 5, username: 'ods', password: 'ods123', full_name: 'Диспетчер ОДС', role: 'dispatcher_ods' as const },
  { id: 6, username: 'technician', password: 'technician123', full_name: 'Техник комплекса', role: 'technician' as const },
];

// ---------- объекты: район → 16 объектов → 78 подобъектов ----------

export interface MockObject {
  id: number;
  name: string;
  level: number;
  kind: string;
  parent_id: number | null;
  children: number[];
}

const L2 = ['Альфа', 'Бета', 'Гамма', 'Дельта', 'Эпсилон', 'Дзета', 'Эта', 'Тета', 'Йота', 'Каппа', 'Лямбда', 'Мю', 'Ню', 'Кси', 'Омикрон', 'Пи'];
const SUB_COUNTS = [2, 6, 5, 5, 4, 5, 6, 4, 5, 5, 4, 6, 5, 5, 6, 5];
const L3 = ['Ро', 'Сигма', 'Фита', 'Тау', 'Ипсилон', 'Фи', 'Хи', 'Пси', 'Омега', 'Ижица', 'Коппа', 'Сампи', 'Дигамма', 'Стигма', 'Хета', 'Шо', 'Санн', 'Йот', 'Аз', 'Буки', 'Веди', 'Глаголь', 'Добро', 'Есть', 'Живете', 'Зело'];

export const OBJECTS = new Map<number, MockObject>();
{
  OBJECTS.set(1, { id: 1, name: 'Район по эксплуатации', level: 1, kind: 'district', parent_id: null, children: [] });
  L2.forEach((n, i) => {
    OBJECTS.set(2 + i, { id: 2 + i, name: `объект ${n}`, level: 2, kind: 'controlHouse', parent_id: 1, children: [] });
    OBJECTS.get(1)!.children.push(2 + i);
  });
  const names = [...L3.map((n) => `объект ${n}`), ...L3.map((n) => `объект ${n}-2`), ...L3.map((n) => `объект ${n}-3`)];
  let id = 18;
  SUB_COUNTS.forEach((c, i) => {
    for (let j = 0; j < c; j++, id++) {
      OBJECTS.set(id, { id, name: names[id - 18], level: 3, kind: 'guardObject', parent_id: 2 + i, children: [] });
      OBJECTS.get(2 + i)!.children.push(id);
    }
  });
}
export const SUBOBJECT_IDS = [...OBJECTS.values()].filter((o) => o.level === 3).map((o) => o.id);

export function objectPath(id: number): string[] {
  const path: string[] = [];
  for (let o = OBJECTS.get(id); o; o = o.parent_id ? OBJECTS.get(o.parent_id) : undefined) path.unshift(o.name);
  return path;
}

/** Все подобъекты в поддереве (датчики висят только на подобъектах) */
export function subtreeIds(id: number): Set<number> {
  const out = new Set<number>();
  const walk = (x: number) => {
    out.add(x);
    OBJECTS.get(x)?.children.forEach(walk);
  };
  walk(id);
  return out;
}

export const objectRef = (id: number) => ({ id, name: OBJECTS.get(id)!.name, path: objectPath(id) });

// ---------- датчики и отказы ----------

export interface MockChannel {
  idx: number;
  id: number;
  name: string;
  kind: SensorKind;
  objectId: number;
  tag: string;
  base: number;
  /** Моменты отказов, по возрастанию */
  faults: number[];
  faultKinds: string[];
  /** «Шумный» датчик: чаще ложные тревоги */
  noisy: boolean;
}

export const CHANNELS: MockChannel[] = [];
export const CHANNEL_BY_ID = new Map<number, MockChannel>();
/** Все отказы: [время, индекс датчика], по времени */
export const ALL_FAULTS: [number, number][] = [];

{
  const r = rng(SEED);
  const totalW = SENSOR_KINDS.reduce((s, k) => s + k.weight, 0);
  const pickKind = () => {
    let x = r() * totalW;
    for (const k of SENSOR_KINDS) if ((x -= k.weight) <= 0) return k;
    return SENSOR_KINDS[0];
  };
  const used = new Set<number>([EXAMPLE_CHANNEL_ID]);
  const ids: number[] = [EXAMPLE_CHANNEL_ID];
  while (ids.length < CHANNELS_COUNT) {
    const id = 300_000 + Math.floor(r() * 50_000);
    if (!used.has(id)) {
      used.add(id);
      ids.push(id);
    }
  }
  const sysCode = (s: string) => SYSTEM_TYPES.indexOf(s) + 1;
  ids.forEach((id, idx) => {
    const example = id === EXAMPLE_CHANNEL_ID;
    const kind = example ? SENSOR_KINDS[0] : pickKind();
    const objectId = example ? 20 : SUBOBJECT_IDS[Math.floor(r() * SUBOBJECT_IDS.length)];
    const name = example
      ? 'Дым ПК 1101+2'
      : kind.name
          .replace('{pk}', String(1000 + Math.floor(r() * 400)))
          .replace('{n}', String(1 + Math.floor(r() * 4)))
          .replace('{k}', String(1 + Math.floor(r() * 40)))
          .replace('{phase}', 'ABC'[Math.floor(r() * 3)]);
    let tag = example
      ? '847-1.1.131.2'
      : `847-${sysCode(kind.system)}.${1 + Math.floor(r() * 4)}.${objectId + 100}.${1 + Math.floor(r() * 20)}`;
    if (!example && r() < 0.3) tag += `.${1 + Math.floor(r() * 9)}`;
    const base = (kind.base ?? 0) + (r() * 2 - 1) * (kind.spread ?? 0);
    // Отказы — пуассоновский поток с индивидуальной интенсивностью
    const rate = kind.faultRate * Math.exp((r() * 2 - 1) * 0.8) * 1.2;
    const faults: number[] = [];
    const faultKinds: string[] = [];
    let t = DATA_START - 3 * DAY;
    for (;;) {
      t += (-Math.log(1 - r()) / rate) * DAY;
      if (t >= DATA_END + DAY) break;
      faults.push(Math.floor(t / 1000) * 1000);
      faultKinds.push(kind.group === 'power' && r() < 0.5 ? 'Пропадание связи' : r() < 0.85 ? 'Неисправен' : 'Отключено устройство');
    }
    if (example) {
      const i = faults.findIndex((f) => f > EXAMPLE_FAULT_AT);
      const pos = i < 0 ? faults.length : i;
      faults.splice(pos, 0, EXAMPLE_FAULT_AT);
      faultKinds.splice(pos, 0, 'Неисправен');
      // В сутки до примера других отказов быть не должно
      for (let j = faults.length - 1; j >= 0; j--)
        if (faults[j] !== EXAMPLE_FAULT_AT && Math.abs(faults[j] - EXAMPLE_FAULT_AT) < 2 * DAY) {
          faults.splice(j, 1);
          faultKinds.splice(j, 1);
        }
    }
    const ch: MockChannel = { idx, id, name, kind, objectId, tag, base, faults, faultKinds, noisy: r() < 0.03 };
    CHANNELS.push(ch);
    CHANNEL_BY_ID.set(id, ch);
    faults.forEach((f) => ALL_FAULTS.push([f, idx]));
  });
  ALL_FAULTS.sort((a, b) => a[0] - b[0]);
}

/** Индекс первого отказа строго позже t */
export function nextFaultIdx(ch: MockChannel, t: number): number {
  let lo = 0;
  let hi = ch.faults.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (ch.faults[mid] <= t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

export function faultWithin(ch: MockChannel, from: number, to: number): number | null {
  const i = nextFaultIdx(ch, from);
  return i < ch.faults.length && ch.faults[i] <= to ? ch.faults[i] : null;
}

// ---------- прогнозы ----------

export const snapTime = (s: number) => DATA_START + s * STEP;

/** Последний срез не позже t; -1 — данных ещё нет */
export function snapIndexAt(t: number): number {
  if (t < DATA_START) return -1;
  return Math.min(SNAPSHOTS - 1, Math.floor((t - DATA_START) / STEP));
}

export const predictionId = (s: number, idx: number) => 50_000 + s * CHANNELS_COUNT + idx;
export function decodePrediction(pid: number): { s: number; idx: number } | null {
  const x = pid - 50_000;
  if (x < 0) return null;
  const s = Math.floor(x / CHANNELS_COUNT);
  if (s >= SNAPSHOTS) return null;
  return { s, idx: x % CHANNELS_COUNT };
}

function computeProb(ch: MockChannel, s: number): number {
  const t = snapTime(s);
  if (ch.id === EXAMPLE_CHANNEL_ID && t === EXAMPLE_AT) return 0.87;
  const f = faultWithin(ch, t, t + DAY);
  const u = h(ch.id, s);
  if (f != null) {
    const detected = h(ch.id, Math.floor(f / 60000)) < 0.8;
    if (detected) {
      const lead = (f - t) / DAY; // 0 — отказ вот-вот, 1 — через сутки
      return Math.min(0.98, Math.max(0.52, 0.97 - lead * 0.35 + (u - 0.5) * 0.14));
    }
    return 0.06 + u * 0.3;
  }
  // Недавний отказ — повышенный фон после ремонта
  const i = nextFaultIdx(ch, t);
  const recent = i > 0 && t - ch.faults[i - 1] < 12 * HOUR;
  const fa = ch.noisy ? 0.03 : 0.0025;
  if (u < fa) return 0.52 + h(ch.id, s, 1) * 0.4; // ложная тревога
  if (u < fa + (ch.noisy ? 0.2 : 0.02) || recent) return 0.21 + h(ch.id, s, 2) * 0.28; // «внимание»
  return 0.002 + h(ch.id, s, 3) ** 8 * 0.19;
}

const cache = new Map<number, Float32Array>();
export function probsAt(s: number): Float32Array {
  let arr = cache.get(s);
  if (!arr) {
    arr = new Float32Array(CHANNELS_COUNT);
    for (let i = 0; i < CHANNELS_COUNT; i++) arr[i] = Math.round(computeProb(CHANNELS[i], s) * 100) / 100;
    cache.set(s, arr);
  }
  return arr;
}

export const probOf = (s: number, idx: number): number => Math.round(probsAt(s)[idx] * 100) / 100;
export const healthOf = (prob: number) => Math.round(100 * (1 - prob));
export const riskOf = (health: number): RiskLevel =>
  health >= 80 ? 'normal' : health >= 50 ? 'attention' : health >= 20 ? 'risk' : 'critical';
export const riskOfProb = (prob: number) => riskOf(healthOf(prob));
export const RISK_ORDER: Record<RiskLevel, number> = { normal: 0, attention: 1, risk: 2, critical: 3 };

/** Исход: был ли отказ в течение 24 ч; null — горизонт ещё не закрылся (конец данных) */
export function outcomeOf(s: number, idx: number): { happened: boolean; fault_at: string | null } | null {
  const t = snapTime(s);
  if (t + DAY > DATA_END) return null;
  const f = faultWithin(CHANNELS[idx], t, t + DAY);
  return f != null ? { happened: true, fault_at: new Date(f).toISOString().slice(0, 19) } : { happened: false, fault_at: null };
}

type MockFactor = FactorDetail & { human: string };

const plural = (n: number, one: string, few: string, many: string) => {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many;
};

/** Причины в двух видах, как у бэкенда: техническая фраза ML (`phrase`) и понятная диспетчеру (`human`) */
function mockFactors(s: number, idx: number): MockFactor[] {
  const ch = CHANNELS[idx];
  const t = snapTime(s);
  if (ch.id === EXAMPLE_CHANNEL_ID && t === EXAMPLE_AT)
    return [
      { feature: 'fault_msgs_24h', value: 6, norm: 0, phrase: 'Сообщений о неисправности за сутки: 6 (обычно 0)', human: '6 сообщений «Неисправен» за сутки (обычно 0)' },
      { feature: 'status_changes_24h', value: 40, norm: 2, phrase: 'Смен статуса за сутки: 40', human: 'Статус менялся 40 раз за сутки (обычно 2) — дребезг' },
      { feature: 'object_faults_7d', value: 3, norm: 0, phrase: 'Отказов в этом объекте за 7 дней: 3', human: '3 отказа у других датчиков этого объекта за 7 дней' },
    ];
  const p = probOf(s, idx);
  const sev = p; // чем выше вероятность, тем ярче признаки
  const pool: [number, MockFactor][] = [];
  const add = (w: number, feature: string, value: number | string, norm: number | string | null, phrase: string, human: string) =>
    pool.push([w + h(ch.id, s, feature.length, 9) * 0.3, { feature, value, norm, phrase, human }]);
  const n = (a: number, b: number, k: number) => Math.round(a + (b - a) * sev * (0.6 + h(ch.id, s, k) * 0.4));
  if (ch.kind.group === 'numeric') {
    const stuck = n(5, 85, 11);
    add(sev, 'stuck_share_24h', stuck, 15, `Одинаковых показаний подряд за сутки: ${stuck}% (обычно до 15%)`, `Показания «застыли»: ${stuck} % одинаковых подряд за сутки (обычно до 15 %)`);
    const dev = Math.round((0.3 + sev * 4 * h(ch.id, s, 12)) * 10) / 10;
    const devS = String(dev).replace('.', ',');
    add(sev * 0.9, 'neighbor_deviation', dev, 1, `Отклонение от соседних датчиков того же типа: ${devS}σ (обычно до 1σ)`, `Показания отличаются от соседних датчиков того же типа на ${devS}σ (обычно до 1σ)`);
    const trend = n(1, 60, 13);
    add(sev * 0.7, 'trend_24h', trend, 5, `Изменение показаний за сутки: +${trend}% (обычно до 5%)`, `Показания выросли на ${trend} % за сутки (обычно до 5 %)`);
  }
  if (ch.kind.group === 'unit') {
    const starts = n(6, 60, 14);
    add(sev, 'starts_24h', starts, 6, `Пусков за сутки: ${starts} (обычно 6)`, `${starts} ${plural(starts, 'пуск', 'пуска', 'пусков')} за сутки (обычно 6) — частые пуски`);
  }
  if (ch.kind.group === 'power') {
    const off = n(0, 14, 15);
    add(sev, 'power_off_24h', off, 0, `Сообщений «Обесточен» за сутки: ${off} (обычно 0)`, `${off} ${plural(off, 'сообщение', 'сообщения', 'сообщений')} «Обесточен» за сутки (обычно 0)`);
  }
  const faults = n(0, 9, 16);
  add(sev * 1.1, 'fault_msgs_24h', faults, 0, `Сообщений о неисправности за сутки: ${faults} (обычно 0)`, `${faults} ${plural(faults, 'сообщение', 'сообщения', 'сообщений')} «Неисправен» за сутки (обычно 0)`);
  const changes = n(2, 48, 17);
  add(sev, 'status_changes_24h', changes, 2, `Смен статуса за сутки: ${changes} (обычно 2)`, `Статус менялся ${changes} ${plural(changes, 'раз', 'раза', 'раз')} за сутки (обычно 2)${changes >= 10 ? ' — дребезг' : ''}`);
  const unc = n(0, 40, 18);
  add(sev * 0.8, 'uncertain_share_24h', unc, 0, `Доля статуса «Неопределен» за сутки: ${unc}% (обычно 0%)`, `«Неопределен» — ${unc} % сообщений за сутки (обычно 0 %)`);
  const silence = n(1, 14, 19);
  add(sev * 0.6, 'max_silence_h', silence, 2, `Самая долгая пауза связи: ${silence} ч (обычно до 2 ч)`, `Самая долгая пауза в связи за последнее время: ${silence} ч (обычно не дольше 2 ч)`);
  const objFaults = n(0, 5, 20);
  if (objFaults > 0)
    add(0.4 + sev * 0.4, 'object_faults_7d', objFaults, 0, `Отказов в этом объекте за 7 дней: ${objFaults}`, `${objFaults} ${plural(objFaults, 'отказ', 'отказа', 'отказов')} у других датчиков этого объекта за 7 дней`);
  const fi = nextFaultIdx(ch, t);
  if (fi > 0) {
    const days = Math.max(1, Math.round((t - ch.faults[fi - 1]) / DAY));
    add(0.3 + sev * 0.3, 'days_since_fault', days, null, `Дней с последнего отказа: ${days}`, `Последний отказ — ${days} сут назад`);
  }
  return pool
    .sort((a, b) => b[0] - a[0])
    .slice(0, 3)
    .map(([, f]) => f);
}

export function factorsOf(s: number, idx: number): FactorDetail[] {
  return mockFactors(s, idx).map(({ feature, value, norm, phrase }) => ({ feature, value, norm, phrase }));
}

const shorten = (t: string) => (t.length <= 60 ? t : `${t.slice(0, 59).replace(/\s+\S*$/, '')}…`);

export function humanFactorsOf(s: number, idx: number): FactorHuman[] {
  return mockFactors(s, idx).map((f) => ({ text: f.human, short: shorten(f.human), features: [f.feature], tech: [f.phrase] }));
}

// ---------- дерево объектов с риском ----------

export interface ChannelFilter {
  objectIds?: Set<number> | null;
  system_type?: string | null;
  sensor_type?: string | null;
  q?: string | null;
}

export function matchChannel(ch: MockChannel, f: ChannelFilter): boolean {
  if (f.objectIds && !f.objectIds.has(ch.objectId)) return false;
  if (f.system_type && ch.kind.system !== f.system_type) return false;
  if (f.sensor_type && ch.kind.sensor !== f.sensor_type) return false;
  if (f.q) {
    const q = f.q.trim().toLowerCase();
    if (q && !ch.name.toLowerCase().includes(q) && !ch.tag.toLowerCase().includes(q) && !String(ch.id).includes(q))
      return false;
  }
  return true;
}

export function objectNodes(s: number, f: ChannelFilter): Map<number, ObjectNode> {
  const nodes = new Map<number, ObjectNode>();
  for (const o of OBJECTS.values())
    nodes.set(o.id, {
      id: o.id,
      name: o.name,
      level: o.level,
      kind: o.kind,
      parent_id: o.parent_id,
      max_risk_level: null,
      risk_counts: { normal: 0, attention: 0, risk: 0, critical: 0 },
      channels_count: 0,
      children: [],
    });
  const probs = s >= 0 ? probsAt(s) : null;
  for (const ch of CHANNELS) {
    if (!matchChannel(ch, f)) continue;
    const level = probs ? riskOfProb(probs[ch.idx]) : null;
    for (let o: MockObject | undefined = OBJECTS.get(ch.objectId); o; o = o.parent_id ? OBJECTS.get(o.parent_id) : undefined) {
      const n = nodes.get(o.id)!;
      n.channels_count += 1;
      if (level) {
        n.risk_counts[level] = (n.risk_counts[level] ?? 0) + 1;
        if (!n.max_risk_level || RISK_ORDER[level] > RISK_ORDER[n.max_risk_level]) n.max_risk_level = level;
      }
    }
  }
  return nodes;
}
