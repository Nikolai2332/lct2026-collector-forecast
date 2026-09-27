import { createContext, useContext } from 'react';
import type { SimStateOut } from '@/types';

export interface DataMoment {
  /** Момент по умолчанию для режима «Сейчас»; undefined — данные свежие или идёт симуляция (момент задаёт сервер) */
  defaultAt?: string;
  /** Последний срез прогнозов в базе */
  lastSnapshot?: string | null;
  /** Идущая симуляция потока; undefined — симуляции нет */
  sim?: SimStateOut;
}

export const DataMomentCtx = createContext<DataMoment>({});

export function useDataMomentDefaults(): DataMoment {
  return useContext(DataMomentCtx);
}
