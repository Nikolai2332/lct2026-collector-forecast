/**
 * Проверка рекомендаций по ТО и настроек в браузере на двух разрешениях:
 *   node scripts/recs-check.mjs [адрес] [папка] [at] [id датчика] [--write]
 * По умолчанию http://localhost:8080, ../docs/screenshots/recommendations, 2026-08-01T12:00:00, 334609.
 * Карточка датчика с блоком «Рекомендация», форма заявки из рекомендации (не сохраняется), вкладка
 * «Рекомендованные работы», окно «Настройки» администратора (не сохраняется).
 * --write — ещё и создать черновик из «Рекомендованных работ» и удалить его (только для изолированного стека).
 * Код 1 — если нашлись проблемы (ошибки в консоли, горизонтальная прокрутка, пустые блоки).
 */
import { mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2).filter((a) => !a.startsWith('--'));
const write = process.argv.includes('--write');
const [base = 'http://localhost:8080', dir = '../docs/screenshots/recommendations', at = '2026-08-01T12:00:00', channel = '334609'] = args;
const outDir = resolve(root, dir);
await mkdir(outDir, { recursive: true });

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
    await page.screenshot({ path: resolve(outDir, `${w}_${name}.png`), fullPage: full });
    console.log('  📷', `${w}_${name}.png`);
  };
  const login = async (role) => {
    await page.goto(`${base}/login`);
    await page.evaluate(() => localStorage.clear());
    await page.goto(`${base}/login`);
    await page.getByRole('button', { name: role }).click();
    await page.getByRole('heading', { name: 'Дашборд' }).waitFor({ timeout: 30_000 });
  };

  await login('Инженер');
  // Карточка датчика: прогноз и рядом рекомендация
  await page.goto(`${base}/channels/${channel}?at=${at}`);
  await page.locator('[data-testid="advice-action"]').waitFor({ timeout: 30_000 });
  const action = (await page.locator('[data-testid="advice-action"]').innerText()).trim();
  const reason = (await page.locator('[data-testid="advice-reason"]').innerText()).trim();
  if (!action || reason.length < 20) problems.push(`${w}: пустая рекомендация`);
  console.log('   рекомендация:', action.slice(0, 90), '|', reason.slice(0, 120));
  await shot('01_channel_recommendation');

  // Форма заявки из рекомендации — заполнена, но не сохраняем
  await page.getByRole('button', { name: 'Заявка по рекомендации' }).click();
  const modal = page.locator('.ant-modal');
  await modal.getByText('Заполнено по рекомендации').waitFor({ timeout: 15_000 });
  const text = await modal.locator('textarea').first().inputValue();
  if (text.trim() !== action) problems.push(`${w}: «Что сделать» в форме не совпадает с рекомендацией`);
  await page.waitForTimeout(400);
  await page.screenshot({ path: resolve(outDir, `${w}_02_work_order_from_recommendation.png`) });
  console.log('  📷', `${w}_02_work_order_from_recommendation.png`);
  await modal.getByRole('button', { name: 'Отмена' }).click();

  // Вкладка «Рекомендованные работы»
  await page.goto(`${base}/work-orders?at=${at}`);
  await page.getByText('Рекомендованные работы', { exact: true }).click();
  await page.getByText('Профилактика по правилам ТО').waitFor({ timeout: 30_000 });
  await idle();
  const rows = await page.locator('.ant-table-row').count();
  if (!rows) problems.push(`${w}: «Рекомендованные работы» пусты`);
  await shot('03_plan');
  if (write && w === 1920) {
    const box = page.locator('.ant-table-row-level-1 .ant-checkbox-input').first();
    await box.check();
    await page.getByRole('button', { name: /Создать черновики заявок/ }).click();
    await page.getByText(/Черновиков создано: 1/).waitFor({ timeout: 15_000 });
    const msg = await page.locator('.ant-message').innerText();
    console.log('   ', msg.replace(/\s+/g, ' ').slice(0, 160));
    await page.screenshot({ path: resolve(outDir, `${w}_04_plan_drafts_created.png`) });
    const number = /ЗН-\d{4}-\d{6}/.exec(msg)?.[0];
    if (!number) problems.push('черновик не создан');
    else {
      // Удаляем созданный черновик через API под тем же пользователем
      const deleted = await page.evaluate(async (num) => {
        const token = localStorage.getItem('collector.token');
        const hdr = { Authorization: `Bearer ${token}` };
        const list = await (await fetch(`/api/work-orders?q=${encodeURIComponent(num)}`, { headers: hdr })).json();
        const id = list.items?.[0]?.id;
        return id ? (await fetch(`/api/work-orders/${id}`, { method: 'DELETE', headers: hdr })).status : 0;
      }, number);
      console.log('    черновик', number, 'удалён:', deleted);
    }
  }

  // Настройки администратора: только открыть и посмотреть
  await login('Администратор');
  await page.getByRole('button', { name: /Администратор системы|Меню пользователя/ }).first().click();
  const settingsItem = page.getByText('Настройки', { exact: true });
  if (await settingsItem.count()) {
    await settingsItem.first().click();
    await page.locator('.ant-drawer').getByText('Границы уровней риска').waitFor({ timeout: 15_000 });
    await page.waitForTimeout(500);
    await page.screenshot({ path: resolve(outDir, `${w}_05_settings.png`) });
    console.log('  📷', `${w}_05_settings.png`);
  } else problems.push(`${w}: нет пункта «Настройки» у администратора`);
  await ctx.close();
}
await browser.close();
if (problems.length) {
  console.log('\nПроблемы:\n' + problems.map((p) => ' - ' + p).join('\n'));
  process.exit(1);
}
console.log('\nПроблем не найдено');
