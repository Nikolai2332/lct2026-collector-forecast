/**
 * Скриншоты всех экранов: npm run screenshots — на моках; node scripts/screenshots.mjs http://localhost:18080 —
 * на работающем стеке с демо-сидом (так сняты docs/screenshots: только демо-данные, не данные заказчика).
 * Без адреса поднимает Vite с VITE_USE_MOCKS=true, проходит экраны в Chromium (Playwright) и сохраняет PNG
 * 1920×1080 в docs/screenshots/ и 1366×768 в docs/screenshots/1366x768/.
 * Падает с кодом 1, если в консоли браузера были ошибки или страница шире окна.
 * Нужен браузер: npx playwright install chromium
 */
import { mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { createServer } from 'vite';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const outDir = resolve(root, '../docs/screenshots');
const AT = '2026-08-01T12:00:00';
const TOKEN_KEY = 'collector.token'; // как в src/api/client.ts

const external = process.argv[2]?.replace(/\/$/, '');
let server = null;
if (!external) {
  process.env.VITE_USE_MOCKS = 'true';
  server = await createServer({ root, logLevel: 'error', server: { port: 5199, strictPort: false } });
  await server.listen();
}
const base = external ?? server.resolvedUrls.local[0].replace(/\/$/, '');

const VIEWPORTS = [
  { width: 1920, height: 1080, dir: outDir },
  { width: 1366, height: 768, dir: resolve(outDir, '1366x768') },
];

const problems = [];
const browser = await chromium.launch();

async function shoot(page, dir, name, { full = false, print = false } = {}) {
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(700);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (overflow > 0 && !print) problems.push(`${name}: горизонтальная прокрутка ${overflow}px`);
  await page.screenshot({ path: resolve(dir, `${name}.png`), fullPage: full });
  console.log('  ✓', name);
}

try {
  for (const vp of VIEWPORTS) {
    await mkdir(vp.dir, { recursive: true });
    console.log(`Экран ${vp.width}×${vp.height} → ${vp.dir}`);
    const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, locale: 'ru-RU' });
    const page = await ctx.newPage();
    page.on('console', (m) => m.type() === 'error' && problems.push(`[${vp.width}] console: ${m.text().slice(0, 200)}`));
    page.on('pageerror', (e) => problems.push(`[${vp.width}] pageerror: ${e.message}`));
    const go = async (path, waitFor) => {
      await page.goto(base + path);
      if (waitFor) await page.locator(waitFor).first().waitFor({ timeout: 20_000 });
    };

    await go('/login', 'form');
    await shoot(page, vp.dir, '01_login');
    // Диспетчер ОДС видит все объекты и принимает решения (роли заказчика — docs/SECURITY.md)
    await page.getByRole('button', { name: 'Диспетчер ОДС', exact: true }).click();
    await page.getByRole('heading', { name: 'Дашборд' }).waitFor();

    await go(`/dashboard?at=${AT}`, '.ant-table-row');
    await page.locator('.tile').first().waitFor();
    await shoot(page, vp.dir, '02_dashboard');

    await go(`/objects?at=${AT}&object=3`, '.ant-table-row');
    await shoot(page, vp.dir, '03_objects');

    await go(`/objects?at=${AT}&view=scheme`, 'canvas');
    await page.waitForTimeout(800);
    await shoot(page, vp.dir, '03b_collector_scheme');

    await go(`/channels/334609?at=${AT}`, 'canvas');
    await shoot(page, vp.dir, '04_channel');
    await page.getByRole('button', { name: 'Отметить решение' }).click();
    const dlg = page.getByRole('dialog', { name: 'Решение диспетчера' });
    await dlg.getByText('Выезд бригады').click();
    await dlg.getByLabel('Причина (из справочника)').click();
    await page.locator('.ant-select-item-option').first().click();
    await shoot(page, vp.dir, '04b_decision_modal');
    await dlg.getByRole('button', { name: 'Сохранить решение' }).click();
    await page.getByText('сохранено и появится в журнале').waitFor();
    await page.getByRole('button', { name: 'Создать заявку' }).click();
    await page.getByRole('dialog', { name: 'Заявка на обслуживание' }).getByLabel('Исполнитель').waitFor();
    await page.waitForTimeout(500);
    await shoot(page, vp.dir, '04c_work_order_form');
    await page.getByRole('button', { name: 'Сохранить черновик' }).click();
    await page.getByText(/Черновик заявки .* сохранён/).waitFor();

    await go(`/journal?at=${AT}`, '.ant-table-row');
    await shoot(page, vp.dir, '05_journal');
    await go(`/journal?at=${AT}&tab=events`, '.ant-table-row');
    await shoot(page, vp.dir, '05b_events');

    await go(`/work-orders?at=${AT}`, '.ant-table-row');
    await shoot(page, vp.dir, '06_work_orders');
    await page.locator('.ant-table-row a').first().click();
    await page.getByRole('heading', { name: /^Заявка / }).waitFor();
    await shoot(page, vp.dir, '06b_work_order');
    await page.emulateMedia({ media: 'print' });
    await shoot(page, vp.dir, '06c_work_order_print', { full: true, print: true });
    await page.emulateMedia({ media: 'screen' });
    // Свой черновик удаляем: по датчику может быть открыта только одна заявка, следующему проходу нужна кнопка
    // «Создать заявку» на той же карточке
    const woId = Number(new URL(page.url()).pathname.split('/').pop());
    const deleted = await page.evaluate(async ({ id, key }) => {
      const r = await fetch(`/api/work-orders/${id}`, { method: 'DELETE', headers: { Authorization: `Bearer ${localStorage.getItem(key)}` } });
      return r.status;
    }, { id: woId, key: TOKEN_KEY });
    if (deleted !== 204) throw new Error(`черновик ${woId} не удалён: ${deleted}`);

    await go(`/quality?at=2026-08-31T12:00:00`, 'canvas');
    await shoot(page, vp.dir, '07_quality');

    // Критическое уведомление: тестовая рассылка, как POST /api/notifications/test на бэкенде
    await go(`/dashboard?at=${AT}`, '.ant-table-row');
    await page.evaluate(async (at) => {
      // Рассылка доступна инженеру и администратору — берём токен демо-инженера, экран остаётся у диспетчера
      const login = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: 'engineer', password: 'engineer123' }),
      });
      const { access_token } = await login.json();
      await fetch(`/api/notifications/test?at=${at}`, { method: 'POST', headers: { Authorization: `Bearer ${access_token}` } });
    }, AT);
    await page.locator('.ant-notification-notice').first().waitFor();
    await page.getByRole('button', { name: /Уведомления/ }).click();
    await page.locator('.ant-popover').waitFor();
    await shoot(page, vp.dir, '08_notifications');

    // Состояние «пусто»: момент до начала данных
    await page.keyboard.press('Escape');
    await go('/dashboard?at=2026-06-01T12:00:00', '[data-state="empty"]');
    await shoot(page, vp.dir, '09_empty_state');

    // Тёмная тема (ночная смена): переключатель в шапке, выбор запоминается
    await go(`/dashboard?at=${AT}`, '.ant-table-row');
    await page.getByRole('button', { name: 'Включить тёмную тему' }).click();
    await page.waitForTimeout(500);
    await shoot(page, vp.dir, '10_dark_dashboard');
    await go(`/objects?at=${AT}&view=scheme`, 'canvas');
    await page.waitForTimeout(800);
    await shoot(page, vp.dir, '10b_dark_scheme');
    await go(`/channels/334609?at=${AT}`, 'canvas');
    await shoot(page, vp.dir, '10c_dark_channel');
    await page.getByRole('button', { name: 'Включить светлую тему' }).click();

    // Роли заказчика: техник видит один комплекс; администратор — «Пользователи и роли»
    await page.getByRole('button', { name: 'Выйти' }).click();
    await go('/login', 'form');
    await page.getByRole('button', { name: 'Техник', exact: true }).click();
    await page.getByRole('heading', { name: 'Дашборд' }).waitFor();
    await go(`/objects?at=${AT}`, '.ant-card');
    await shoot(page, vp.dir, '11_technician_objects');
    await page.getByRole('button', { name: 'Выйти' }).click();
    await go('/login', 'form');
    await page.getByRole('button', { name: 'Администратор', exact: true }).click();
    await page.getByRole('heading', { name: 'Дашборд' }).waitFor();
    await go('/admin/users', '.ant-table-row');
    await shoot(page, vp.dir, '12_users_roles');
    await ctx.close();
  }
} finally {
  await browser.close();
  await server?.close();
}

if (problems.length) {
  console.error('\nПроблемы:\n' + problems.join('\n'));
  process.exit(1);
}
console.log('\nГотово, ошибок в консоли нет.');
