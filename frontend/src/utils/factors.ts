import type { FactorHuman } from '@/types';

export interface FactorSource {
  factors: string[];
  factors_human?: FactorHuman[];
}

/** Понятные причины; если бэкенд старый и factors_human нет — технические фразы как есть */
export function humanFactors(p: FactorSource): FactorHuman[] {
  if (p.factors_human?.length) return p.factors_human;
  return p.factors.map((f) => ({ text: f, short: f.length > 60 ? `${f.slice(0, 59)}…` : f, features: [], tech: [f] }));
}
