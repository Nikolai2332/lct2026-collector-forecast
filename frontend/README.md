# Интерфейс диспетчера

React 18 + TypeScript + Vite, Ant Design 5 (`ru_RU`), ECharts, TanStack Query, React Router, MSW, SSE.

## Запуск

```bash
npm ci
npm run dev            # http://localhost:5173, на моках (по умолчанию)
```

Демо-пользователи — кнопки быстрого входа на экране входа: `dispatcher`, `engineer`, `manager`, `admin` (пароль — логин + `123`).

### Реальный API

```bash
docker compose up -d db api                       # из корня репозитория
VITE_USE_MOCKS=false npm run dev                  # Windows PowerShell: $env:VITE_USE_MOCKS='false'; npm run dev
```

Dev-сервер проксирует `/api` на `http://localhost:8000` (другой адрес — `API_PROXY_TARGET`).

### В Docker

Из корня: `docker compose up --build` — поднимет `db`, `api` и `frontend` (<http://localhost:8080>). Nginx раздаёт статику и проксирует `/api` на `api:8000`, SSE — без буферизации. Моки в контейнере: `FRONTEND_USE_MOCKS=true` — переключение без пересборки (пишется в `config.js` при старте).

## Скрипты

| Команда | Что делает |
|---|---|
| `npm run dev` | dev-сервер Vite |
| `npm run build` | `tsc -b` + сборка в `dist/` |
| `npm run lint` | ESLint |
| `npm run typecheck` | `tsc -b --noEmit` |
| `npm run gen:api` | типы из `../backend/openapi.json` → `src/types/api.ts` |
| `npm run check:real` | Playwright на реальном API (`docker compose up -d`, http://localhost:8080): все экраны в режиме «Сейчас», проверка реальных цифр ML (момент по умолчанию, точность за 30 дней, ползунок порога, карточка датчика и заявки, экран качества). Создаёт черновик заявки и в конце удаляет его (`DELETE /api/work-orders/{id}`), тестовых заявок не остаётся; `CREATE_WORK_ORDER=false` — не создавать. Скриншоты — в `check-real/` |
| `npm run screenshots` | Playwright на моках проходит все экраны → `../docs/screenshots/` (1920×1080) и `../docs/screenshots/1366x768/`. На стеке с демо-сидом: `node scripts/screenshots.mjs http://localhost:18080` (так сняты текущие). Перед первым запуском: `npx playwright install chromium` |
| `node scripts/polish-shots.mjs <адрес> <папка> <роль>` | проход по всем экранам одной ролью на 1920×1080 и 1366×768: скриншоты, проверка горизонтальной прокрутки и ошибок консоли; только чтение. Скриншоты реальных данных — только в `backups/` |

## Структура

```
src/
  api/         клиент fetch (токен, 401 → вход), эндпоинты, общие запросы и инвалидация
  mocks/       MSW: демо-мир, обработчики всех эндпоинтов, эмуляция SSE
  components/  шапка и меню, RiskBadge, QueryState, модалки решения и заявки, графики, плитки
  pages/       7 экранов + страница заявки
  hooks/       авторизация, «машина времени» (?at=), уведомления
  utils/       конфиг, время, подписи, уровни риска
  types/       типы из openapi.json
docker/        nginx.conf и скрипт рантайм-конфига
```

Принятые решения и замечания к бэкенду — [`docs/FRONTEND_DECISIONS.md`](../docs/FRONTEND_DECISIONS.md).
