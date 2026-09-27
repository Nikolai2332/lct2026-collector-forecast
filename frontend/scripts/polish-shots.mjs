/**
 * Проход по всем экранам под ролью и скриншоты на двух разрешениях:
 *   node scripts/polish-shots.mjs [адрес] [папка] [роль] [at]
 * По умолчанию http://localhost:8080, ../backups/polish_screenshots/now, dispatcher, режим «Сейчас».
 * Проверяет: нет горизонтальной прокрутки страницы, нет ошибок в консоли, нет обрезанных заголовков шапки.
 * Неразрушающий: только чтение. Код 1 — если нашлись проблемы.
 */
import { mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const [base = 'http://localhost:8080', dir = '../backups/polish_screenshots/now', role = 'dispatcher', at = ''] = process.argv.slice(2);
const outDir = resolve(root, dir);
await mkdir(outDir, { recursive: true });
const ROLE_BUTTON = { dispatcher: 'Диспетчер', engineer: 'Инженер', manager: 'Руководитель', admin: 'Администратор' };
const q = at ? `?at=${at}` : '';

const problems = [];
const browser = await chromium.launch();
for (const [w, h] of [
  [1920, 1080],
  [1366, 768],
]) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, locale: 'ru-RU' });
  const page = await ctx.newPage();
  page.on('console', (m) => m.type() === 'error' && problems.push(`${w}: console: ${m.text().slice(0, 200)}`));
  page.on('pageerror', (e) => problems.push(`${w}: pageerror: ${e.message}`));
  const idle = async () => {
    await page.waitForLoadState('networkidle').catch(() => {});
    await page.locator('.ant-skeleton').first().waitFor({ state: 'detached', timeout: 30_000 }).catch(() => {});
    await page.waitForTimeout(700);
  };
  const shot = async (name, full = true) => {
    await idle();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    if (overflow > 1) problems.push(`${w}: ${name}: горизонтальная прокрутка ${overflow}px`);
    await page.screenshot({ path: resolve(outDir, `${w}_${role}_${name}.png`), fullPage: full });
    console.log('  📷', `${w}_${role}_${name}.png`);
  };

  await page.goto(`${base}/login`);
  await shot('00_login', false);
  await page.getByRole('button', { name: ROLE_BUTTON[role] }).click();
  await page.getByRole('heading', { name: 'Дашборд' }).waitFor({ timeout: 30_000 });
  if (q) await page.goto(`${base}/dashboard${q}`);
  await shot('01_dashboard');

  // Колокольчик
  await page.getByRole('button', { name: /Уведомления/ }).click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: resolve(outDir, `${w}_${role}_02_bell.png`) });
  await page.keyboard.press('Escape');
  await page.mouse.click(5, h - 5);

  // Карточка самого опасного датчика из топ-20
  const first = page.locator('.ant-card:has-text("Топ-20") .ant-table-row a').first();
  if (await first.count()) {
    await first.click();
    await page.getByText('Почему модель так считает').waitFor({ timeout: 30_000 }).catch(() => {});
    await shot('03_channel');
  }
  for (const [path, name] of [
    ['/objects', '04_objects'],
    ['/journal', '05_journal'],
    ['/work-orders', '06_work_orders'],
    ['/quality', '07_quality'],
  ]) {
    await page.goto(`${base}${path}${q}`);
    await shot(name);
  }
  const header = await page.evaluate(() => {
    const el = document.querySelector('.app-header');
    return el ? el.scrollWidth - el.clientWidth : 0;
  });
  if (header > 1) problems.push(`${w}: шапка не помещается на ${header}px`);
  await ctx.close();
}
await browser.close();
if (problems.length) {
  console.log('Проблемы:\n' + problems.map((p) => ' - ' + p).join('\n'));
  process.exit(1);
}
console.log('Без проблем');
