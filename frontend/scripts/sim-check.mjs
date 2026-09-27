/**
 * Проверка симуляции потока в браузере против работающего стека: node scripts/sim-check.mjs [адрес] [сутки] [скорость]
 * По умолчанию http://localhost:8080, 2026-08-01 (демо-данные), ×600.
 * Входит инженером, запускает симуляцию кнопкой, следит за меткой, дашбордом, уведомлениями и скрытием исходов,
 * останавливает и проверяет возврат в обычный режим. Скриншоты — в docs/screenshots/simulation/.
 * Неразрушающая: только старт и стоп симуляции. Код 1 — если проверка не прошла или в консоли были ошибки.
 */
import { mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const [base = 'http://localhost:8080', day = '2026-08-01', speed = '600', prefix = ''] = process.argv.slice(2);
const outDir = resolve(root, '../docs/screenshots/simulation');
await mkdir(outDir, { recursive: true });

const problems = [];
const log = (...a) => console.log(...a);
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 }, locale: 'ru-RU' });
const page = await ctx.newPage();
page.on('console', (m) => m.type() === 'error' && problems.push(`console: ${m.text().slice(0, 200)}`));
page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));

const shoot = async (name) => {
  await page.waitForTimeout(500);
  await page.screenshot({ path: resolve(outDir, `${prefix}${name}.png`) });
  log('  📷', `${prefix}${name}.png`);
};
const api = (path, method = 'GET') =>
  page.evaluate(
    async ([p, m]) => {
      const r = await fetch(p, { method: m, headers: { Authorization: `Bearer ${localStorage.getItem('collector.token')}` } });
      return r.json();
    },
    [path, method],
  );
const badge = page.getByTestId('sim-badge');
const badgeTime = async () => (await badge.textContent())?.match(/(\d\d:\d\d)$/)?.[1] ?? '';
const topRows = async () =>
  page.locator('.ant-card:has-text("Топ-20 датчиков по риску") .ant-table-row').allInnerTexts();

try {
  await page.goto(`${base}/login`);
  await page.getByRole('button', { name: 'Инженер' }).click();
  await page.getByRole('heading', { name: 'Дашборд' }).waitFor({ timeout: 30_000 });
  const before = await api('/api/sim/state');
  if (before.active) await api('/api/sim/stop', 'POST');
  const summaryBefore = await api('/api/dashboard/summary');
  log('До симуляции «Сейчас»:', summaryBefore.at, 'срез', summaryBefore.snapshot_at);

  // Запуск кнопкой
  await page.getByRole('button', { name: 'Симуляция' }).click();
  const modal = page.locator('.ant-modal');
  await modal.getByText('Воспроизведение заранее рассчитанных прогнозов').waitFor();
  const dayInput = modal.locator('.ant-picker input');
  await dayInput.click();
  await dayInput.fill(day.split('-').reverse().join('.'));
  await dayInput.press('Enter');
  await modal.locator('.ant-select').click();
  await page.locator('.ant-select-item-option', { hasText: `×${speed} ` }).click();
  await modal.getByRole('button', { name: 'Запустить' }).click();
  await badge.waitFor({ timeout: 15_000 });
  log('Метка:', await badge.textContent());
  await page.locator('.ant-card:has-text("Топ-20 датчиков по риску") .ant-table-row').first().waitFor({ timeout: 20_000 });
  const rowsStart = await topRows();
  await shoot('01_start');

  // Модельные часы идут
  const t1 = await badgeTime();
  await page.waitForTimeout(7_000);
  const t2 = await badgeTime();
  log('Часы:', t1, '→', t2);
  if (!(t2 > t1)) problems.push(`модельные часы не идут: ${t1} → ${t2}`);

  // Будущие исходы скрыты: в режиме симуляции исход виден только у прогнозов с закрытым горизонтом
  const st = await api('/api/sim/state');
  const preds = await api('/api/predictions?limit=200');
  const shown = preds.items.filter((i) => i.outcome).length;
  log(`Модельное время ${st.model_time}; прогнозов ${preds.items.length}, исходов показано ${shown}`);
  if (shown) problems.push(`в симуляции видны исходы незакрытого горизонта: ${shown}`);
  const explicit = await api(`/api/predictions?limit=200&at=${preds.at}`);
  log(`Тот же момент через ?at=: исходов ${explicit.items.filter((i) => i.outcome).length}`);

  // Уведомление о критическом прогнозе
  const notice = page.locator('.ant-notification-notice');
  await notice.first().waitFor({ timeout: 240_000 });
  log('Уведомление:', (await notice.first().locator('.ant-notification-notice-message').textContent())?.trim());
  const bell = await page.locator('.ant-badge-count').first().textContent().catch(() => '');
  log('Счётчик колокольчика:', bell);
  await shoot('03_notification');

  // Середина суток: дашборд обновился сам
  for (let i = 0; i < 120 && (await badgeTime()) < '12:30'; i++) await page.waitForTimeout(1_000);
  await page.waitForTimeout(3_000);
  const rowsMid = await topRows();
  const changed = rowsMid.join('|') !== rowsStart.join('|');
  log('Середина:', await badge.textContent(), '; топ-20 изменился:', changed);
  if (!changed) problems.push('список прогнозов на дашборде не изменился со временем');
  await shoot('02_middle');
  const bell2 = await page.locator('.ant-badge-count').first().textContent().catch(() => '');
  log('Счётчик колокольчика:', bell, '→', bell2);

  // Стоп кнопкой: интерфейс возвращается в обычный режим «Сейчас»
  await page.getByRole('button', { name: 'Остановить симуляцию' }).click();
  await badge.waitFor({ state: 'detached', timeout: 15_000 });
  await page.waitForTimeout(2_000);
  const after = await api('/api/sim/state');
  const summaryAfter = await api('/api/dashboard/summary');
  log('После стопа: active =', after.active, after.stop_reason, '; «Последние данные»:',
    await page.getByText('Последние данные').isVisible());
  if (after.active) problems.push('симуляция не остановилась');
  if (summaryAfter.snapshot_at !== summaryBefore.snapshot_at && !base.includes('8080'))
    log('  (срез без at после стопа:', summaryAfter.snapshot_at, ')');
  await shoot('04_end');
} catch (e) {
  problems.push(`сценарий упал: ${e.message}`);
  await page.screenshot({ path: resolve(outDir, `${prefix}error.png`) }).catch(() => {});
  await api('/api/sim/stop', 'POST').catch(() => {});
} finally {
  await browser.close();
}

if (problems.length) {
  console.error('\nПроблемы:\n- ' + problems.join('\n- '));
  process.exit(1);
}
log('\nПроверка симуляции в браузере пройдена');
