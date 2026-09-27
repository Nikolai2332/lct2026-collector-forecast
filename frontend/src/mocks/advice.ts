/**
 * Упрощённый аналог движка рекомендаций ТО (backend/app/recommendations) для моков: те же поля и коды правил,
 * вывод по группе датчика, уровню риска и отказам за 90 дней. Настоящие правила — на бэкенде.
 */
import type { MaintenanceAdvice, RiskLevel, WorkOrderPriority } from '@/types';
import { DUE_HOURS, PRIORITY_BY_RISK, PRIORITY_LABELS, workOrderForPrediction } from './db';
import { DAY, fmtT } from './time';
import { CHANNELS, type MockChannel, decodePrediction, probOf, riskOfProb, snapTime } from './world';

const SOURCE = 'Проект правил, требует согласования со специалистами эксплуатации';
const PRIORITIES: WorkOrderPriority[] = ['low', 'medium', 'high', 'critical'];

interface MockRule {
  id: string;
  title: string;
  action: string;
  conclusion: string;
  kind: MaintenanceAdvice['fault_kind'];
  assignee: string;
  plan?: boolean;
  raise?: boolean;
  noOrder?: boolean;
  avoid?: string;
}

const RULES: Record<string, MockRule> = {
  power_object: { id: 'power_object', title: 'Питание объекта', kind: 'power', plan: true, assignee: 'Электромонтёры участка',
    action: 'Проверить электропитание объекта: вводной автомат и АВР, автоматы и контакторы щита, напряжение по фазам. Датчики объекта проверять после восстановления питания.',
    conclusion: 'вероятна проблема питания объекта, а не отдельного датчика', avoid: 'Не менять датчики, пока не восстановлено питание объекта' },
  chronic_fault: { id: 'chronic_fault', title: 'Повторные неисправности — диагностика и замена', kind: 'fault', plan: true, raise: true, assignee: 'Бригада КИПиА',
    action: 'Диагностика датчика и его шлейфа; если неисправность подтвердится — замена датчика.', conclusion: 'неисправность повторяется — вероятен износ самого датчика' },
  gas_calibration: { id: 'gas_calibration', title: 'Газоанализатор — калибровка', kind: 'drift', plan: true, assignee: 'Бригада КИПиА (газовый контроль)',
    action: 'Проверить калибровку газоанализатора поверочной смесью; при дрейфе — заменить сенсор.', conclusion: 'показания газоанализатора недостоверны' },
  unit: { id: 'pump_unit', title: 'Агрегат — проверка', kind: 'unknown', plan: true, assignee: 'Бригада водоотведения',
    action: 'Проверить агрегат: питание и пускатель, привод; датчик состояния — после агрегата.', conclusion: 'неисправен может быть сам агрегат' },
  fault_diagnostics: { id: 'fault_diagnostics', title: 'Неисправность датчика — диагностика', kind: 'fault', assignee: 'Бригада КИПиА',
    action: 'Диагностика датчика: контакты, шлейф, крепление и питание; при повторе неисправности — замена.', conclusion: 'датчик сам сообщает о неисправности' },
  verify_remote_first: { id: 'verify_remote_first', title: 'Сначала удалённая проверка', kind: 'unknown', assignee: 'Диспетчер смены, затем бригада КИПиА',
    action: 'Сначала удалённая проверка: камеры, соседние датчики, журнал событий объекта. Выезд — если отсутствие связи подтвердится.',
    conclusion: 'для этого типа датчиков модель часто даёт ложные тревоги', avoid: 'Не направлять бригаду без удалённой проверки' },
  watch: { id: 'watch', title: 'Наблюдение', kind: 'unknown', noOrder: true, assignee: 'Диспетчер смены',
    action: 'Наблюдать: оценить датчик повторно на следующем срезе прогноза; выезд пока не нужен.', conclusion: 'явных признаков конкретной неисправности нет' },
  routine: { id: 'routine', title: 'Плановое ТО', kind: 'unknown', noOrder: true, assignee: '—',
    action: 'Внеочередных работ не требуется — обслуживание по графику.', conclusion: 'риск отказа низкий' },
};

function pickRule(ch: MockChannel, risk: RiskLevel, faults90: number): MockRule {
  if (risk === 'normal') return faults90 >= 3 ? RULES.chronic_fault : RULES.routine;
  if (ch.kind.group === 'power') return RULES.power_object;
  if (faults90 >= 3) return RULES.chronic_fault;
  if (ch.kind.group === 'numeric' && ch.kind.system.startsWith('Газ')) return RULES.gas_calibration;
  if (ch.kind.group === 'unit') return RULES.unit;
  if (ch.noisy && ch.kind.sensor === 'Датчик дыма') return RULES.verify_remote_first;
  if (risk === 'attention') return RULES.watch;
  return RULES.fault_diagnostics;
}

export function mockAdvice(pid: number, at: number): MaintenanceAdvice | null {
  const d = decodePrediction(pid);
  if (!d) return null;
  const ch = CHANNELS[d.idx];
  const t = snapTime(d.s);
  const prob = probOf(d.s, d.idx);
  const risk = riskOfProb(prob);
  const faults90 = ch.faults.filter((f) => f <= t && f > t - 90 * DAY).length;
  const faults30 = ch.faults.filter((f) => f <= t && f > t - 30 * DAY).length;
  const rule = pickRule(ch, risk, faults90);
  let priority: WorkOrderPriority = rule.noOrder ? 'low' : PRIORITY_BY_RISK[risk];
  if (rule.raise) priority = PRIORITIES[Math.min(PRIORITIES.indexOf(priority) + 1, 3)];
  if (rule.id === 'verify_remote_first') priority = 'medium';
  const due = rule.id === 'verify_remote_first' ? 4 : rule.id === 'watch' ? 24 : DUE_HOURS[priority];
  const facts = [
    `вероятность отказа в ближайшие 24 ч — ${Math.round(prob * 100)} %`,
    faults90 ? `отказов за 30 дней — ${faults30}, за 90 дней — ${faults90}` : 'за 90 дней отказов не было',
  ];
  const text = facts.join('; ');
  const open = workOrderForPrediction(pid, at);
  return {
    prediction_id: pid,
    channel_id: ch.id,
    at: fmtT(t),
    rule_id: rule.id,
    title: rule.title,
    action: rule.action,
    reason: `${text[0].toUpperCase()}${text.slice(1)} → ${rule.conclusion}.`,
    facts,
    conclusion: rule.conclusion,
    priority,
    priority_label: PRIORITY_LABELS[priority],
    due_hours: due,
    due_at: fmtT(t + due * 3_600_000),
    assignee: rule.assignee,
    avoid: [...(rule.avoid ? [rule.avoid] : []), ...(open ? [`Не создавать повторную заявку: по датчику уже открыта ${open.number}`] : [])],
    fault_kind: rule.kind,
    fault_kind_label: {
      link: 'Потеря связи', fault: 'Неисправность датчика', disconnected: '«Отключено устройство»', value: 'Сбой значения',
      group: 'Групповое отключение (плановые работы)', power: 'Питание', drift: 'Недостоверные показания', unknown: 'Не определён',
    }[rule.kind],
    fault_kind_basis: 'моки: по типу датчика и истории отказов',
    plan: !!rule.plan,
    work_order_needed: !rule.noOrder,
    recommendation_id: null,
    source: SOURCE,
    rules_version: 'mock',
  };
}

export const ADVICE_SOURCE = SOURCE;
