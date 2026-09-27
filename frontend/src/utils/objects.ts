import type { ObjectNode } from '@/types';

export interface TreeOption {
  value: number;
  title: string;
  children: TreeOption[];
}

/** Дерево объектов для TreeSelect */
export const objectTreeData = (nodes: ObjectNode[]): TreeOption[] =>
  nodes.map((n) => ({ value: n.id, title: n.name, children: objectTreeData(n.children ?? []) }));

export const critOf = (n: ObjectNode) => n.risk_counts.critical ?? 0;
export const riskOf = (n: ObjectNode) => n.risk_counts.risk ?? 0;

/** Куда смотреть в первую очередь: больше критичных, затем больше «риска», затем больше доля критичных */
export function byUrgency(a: ObjectNode, b: ObjectNode): number {
  return (
    critOf(b) - critOf(a) ||
    riskOf(b) - riskOf(a) ||
    critOf(b) / Math.max(1, b.channels_count) - critOf(a) / Math.max(1, a.channels_count) ||
    a.name.localeCompare(b.name, 'ru')
  );
}
