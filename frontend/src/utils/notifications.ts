import type { NotificationItem } from '@/types';

/** Журнал с фильтром «критичные этого среза» — куда ведут сводки «ещё N датчиков перешли в «Критично»» */
export const sliceJournalPath = (slice: string) => `/journal?slice=${encodeURIComponent(slice)}&risk=critical`;

export const notificationPath = (n: NotificationItem) =>
  n.kind === 'critical_summary' ? sliceJournalPath(n.created_at) : `/channels/${n.prediction.channel.id}`;
