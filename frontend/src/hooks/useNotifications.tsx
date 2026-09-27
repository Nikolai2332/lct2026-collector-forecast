import { useQueryClient } from '@tanstack/react-query';
import { App, Button } from 'antd';
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/api/endpoints';
import type { NotificationItem } from '@/types';
import { USE_MOCKS } from '@/utils/config';
import { humanFactors } from '@/utils/factors';
import { notificationPath } from '@/utils/notifications';
import { useAt, useAtNavigate } from './useAt';
import { useAuth } from './useAuth';

const SOUND_KEY = 'collector.sound';
const MAX_ITEMS = 100;
/** Не больше стольких всплывающих окон в минуту; остальное — только в колокольчике */
const POPUPS_PER_MINUTE = 5;
/** Звук не чаще раза в столько миллисекунд — пачка уведомлений одного среза звучит один раз */
const BEEP_GAP_MS = 3_000;

interface NotificationsState {
  items: NotificationItem[];
  unread: number;
  markRead: () => void;
  soundOn: boolean;
  setSoundOn: (v: boolean) => void;
  connected: boolean;
}

const Ctx = createContext<NotificationsState | null>(null);

const readSound = () => {
  try {
    return localStorage.getItem(SOUND_KEY) !== 'off';
  } catch {
    return true;
  }
};

/** Короткий двухтональный сигнал через WebAudio — без звуковых файлов */
function beep() {
  try {
    const AudioCtx = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new AudioCtx();
    [880, 660, 880].forEach((f, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = f;
      osc.type = 'square';
      gain.gain.value = 0.06;
      osc.connect(gain).connect(ctx.destination);
      const t = ctx.currentTime + i * 0.18;
      osc.start(t);
      osc.stop(t + 0.14);
    });
    setTimeout(() => ctx.close(), 1000);
  } catch {
    /* звук недоступен — остаётся всплывающее окно */
  }
}

const keyOf = (n: NotificationItem) => `${n.kind}|${n.id}|${n.created_at}`;

/** Критические уведомления: SSE /api/notifications/stream по одноразовому тикету, в моках — эмуляция по таймеру */
export function NotificationsProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [at] = useAt();
  const nav = useAtNavigate();
  const { notification } = App.useApp();
  const qc = useQueryClient();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [seen, setSeen] = useState<Set<string>>(new Set());
  const [soundOn, setSoundState] = useState(readSound);
  const [connected, setConnected] = useState(false);
  const soundRef = useRef(soundOn);
  soundRef.current = soundOn;
  const navRef = useRef(nav);
  navRef.current = nav;
  const popups = useRef<number[]>([]);
  const lastBeep = useRef(0);

  const setSoundOn = useCallback((v: boolean) => {
    setSoundState(v);
    try {
      localStorage.setItem(SOUND_KEY, v ? 'on' : 'off');
    } catch {
      /* не сохранили — настройка действует до перезагрузки */
    }
  }, []);

  // Снимок — новые переходы в «Критично» за 24 ч. Непрочитанными считаем только последний срез:
  // старые переходы уже показаны в ленте и не должны раздувать счётчик колокольчика.
  const onSnapshot = useCallback((list: NotificationItem[]) => {
    const latest = list.reduce((m, n) => (n.created_at > m ? n.created_at : m), '');
    setItems(list.slice(0, MAX_ITEMS));
    setSeen(new Set(list.filter((n) => n.created_at !== latest).map(keyOf)));
  }, []);

  const onEvent = useCallback(
    (n: NotificationItem) => {
      const mainFactor = humanFactors(n.prediction)[0]?.text;
      setItems((prev) => [n, ...prev.filter((x) => keyOf(x) !== keyOf(n))].slice(0, MAX_ITEMS));
      const now = Date.now();
      popups.current = popups.current.filter((t) => now - t < 60_000);
      if (popups.current.length >= POPUPS_PER_MINUTE) return; // лавина — только счётчик колокольчика
      popups.current.push(now);
      if (soundRef.current && now - lastBeep.current > BEEP_GAP_MS) {
        lastBeep.current = now;
        beep();
      }
      const summary = n.kind === 'critical_summary';
      notification.error({
        key: keyOf(n),
        message: n.title,
        description: (
          <>
            <div>{n.message}</div>
            {!summary && mainFactor && <div style={{ color: 'var(--app-muted)', marginTop: 4 }}>{mainFactor}</div>}
            <Button
              type="primary"
              size="small"
              style={{ marginTop: 8 }}
              onClick={() => {
                notification.destroy(keyOf(n));
                navRef.current(notificationPath(n));
              }}
            >
              {summary ? 'Открыть в журнале' : 'Открыть карточку датчика'}
            </Button>
          </>
        ),
        duration: 15,
        placement: 'bottomRight',
      });
    },
    [notification],
  );

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    let cleanup: () => void = () => {};
    if (USE_MOCKS) {
      import('@/mocks/stream').then(({ subscribe }) => {
        if (cancelled) return;
        setConnected(true);
        cleanup = subscribe(at, onSnapshot, onEvent);
      });
    } else {
      // JWT в адрес потока не кладём (он оседает в логах прокси): перед каждым подключением берём
      // одноразовый тикет. Тикет сгорает при подключении, поэтому встроенный автоповтор EventSource
      // не годится — при обрыве закрываем поток и подключаемся заново с новым тикетом.
      let es: EventSource | null = null;
      let timer: ReturnType<typeof setTimeout> | undefined;
      let failures = 0;
      const connect = async () => {
        let ticket: string;
        try {
          ticket = (await api.sseTicket()).ticket;
        } catch {
          if (!cancelled) schedule();
          return;
        }
        if (cancelled) return;
        const params = new URLSearchParams({ ticket });
        if (at) params.set('at', at);
        es = new EventSource(`/api/notifications/stream?${params}`);
        es.onopen = () => {
          failures = 0;
          setConnected(true);
        };
        es.onerror = () => {
          setConnected(false);
          es?.close();
          es = null;
          if (!cancelled) schedule();
        };
        es.addEventListener('snapshot', (e) => onSnapshot(JSON.parse((e as MessageEvent).data) as NotificationItem[]));
        es.addEventListener('critical_prediction', (e) => onEvent(JSON.parse((e as MessageEvent).data) as NotificationItem));
        // Служебное событие симуляции: новый срез, запуск или остановка — сразу перечитать состояние
        es.addEventListener('sim_clock', () => void qc.invalidateQueries({ queryKey: ['sim-state'] }));
      };
      const schedule = () => {
        failures += 1;
        timer = setTimeout(connect, Math.min(30_000, 2_000 * 2 ** Math.min(failures - 1, 4)));
      };
      void connect();
      cleanup = () => {
        clearTimeout(timer);
        es?.close();
      };
    }
    return () => {
      cancelled = true;
      cleanup();
      setConnected(false);
    };
  }, [user, at, onSnapshot, onEvent, qc]);

  const markRead = useCallback(() => setSeen(new Set(items.map(keyOf))), [items]);
  // Счётчик — число датчиков: сводка «ещё 11 датчиков» добавляет 11
  const unread = items.filter((n) => !seen.has(keyOf(n))).reduce((s, n) => s + (n.count ?? 1), 0);

  const value = useMemo(
    () => ({ items, unread, markRead, soundOn, setSoundOn, connected }),
    [items, unread, markRead, soundOn, setSoundOn, connected],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useNotifications(): NotificationsState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useNotifications вне NotificationsProvider');
  return v;
}
