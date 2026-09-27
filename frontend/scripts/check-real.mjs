/**
 * Проход по всем экранам на реальном API (docker compose up -d, фронтенд на :8080): npm run check:real
 * Режим «Сейчас» (без ?at=). Печатает запросы к API, ошибки консоли и ответы 4xx/5xx, сохраняет скриншоты.
 * Проверяет, что экраны показывают реальные цифры: EXPECT_AT — ожидаемый момент по умолчанию в переключателе
 * (для реального журнала — 29.06.2026 23:00), EXPECT_PRECISION — Precision модели на экране качества.
 * Создаёт черновик заявки из прогноза, чтобы пройти список и карточку заявки, и в конце удаляет его
 * (DELETE /api/work-orders/{id}); CREATE_WORK_ORDER=false — не создавать.
 * BASE_URL — адрес фронтенда (по умолчанию http://localhost:8080), OUT_DIR — куда класть PNG.
 */
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const base = process.env.BASE_URL ?? 'http://localhost:8080';
const outDir = resolve(process.env.OUT_DIR ?? 'check-real');
await mkdir(outDir, { recursive: true });

const expectAt = process.env.EXPECT_AT ?? '29.06.2026 23:00';
const expectPrecision = process.env.EXPECT_PRECISION ?? '69,6';
const problems = [];
const check = (ok, msg) => {
  console.log(`  ${ok ? '✓' : '✗'} ${msg}`);
  if (!ok) problems.push(msg);
};
const apiCalls = [];
let createdWorkOrderId = null;
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, locale: 'ru-RU' });
const page = await ctx.newPage();
page.on('console', (m) => m.type() === 'error' && problems.push(`console: ${m.text().slice(0, 200)}`));
page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
page.on('response', (r) => {
  const u = new URL(r.url());
  if (!u.pathname.startsWith('/api/')) return;
  apiCalls.push(`${r.status()} ${u.pathname}${u.search}`);
  if (r.status() >= 400) problems.push(`HTTP ${r.status()} ${u.pathname}${u.search}`);
});

async function screen(name, path, waitFor) {
  apiCalls.length = 0;
  await page.goto(base + path);
  if (waitFor) await page.locator(waitFor).first().waitFor({ timeout: 30_000 }).catch(() => problems.push(`${name}: не дождались ${waitFor}`));
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(800);
  await page.screenshot({ path: resolve(outDir, `${name}.png`), fullPage: true });
  const empty = await page.locator('.ant-empty').count();
  const text = (await page.locator('main, .ant-layout-content').first().innerText()).replace(/\s+/g, ' ').slice(0, 600);
  console.log(`\n=== ${name} (${page.url().replace(base, '')}) пустых блоков: ${empty}`);
  console.log(`API: ${[...new Set(apiCalls)].join(' | ')}`);
  console.log(`Текст: ${text}`);
}

try {
  await page.goto(base + '/login');
  await page.getByLabel('Логин').fill('dispatcher');
  await page.getByLabel('Пароль').fill('dispatcher123');
  await page.getByRole('button', { name: /Войти/ }).click();
  await page.getByRole('heading', { name: 'Дашборд' }).waitFor({ timeout: 30_000 });

  await screen('01_dashboard', '/dashboard', '.ant-table-row');
  check((await page.getByLabel('Момент времени').inputValue()) === expectAt, `переключатель времени по умолчанию = ${expectAt}`);
  const accuracy = await page.locator('.ant-card', { hasText: 'Точность тревог за последние 30 дней' }).first().innerText();
  check(/Precision\s*\d/.test(accuracy), `точность за 30 дней заполнена: ${accuracy.replace(/\s+/g, ' ')}`);
  const slider = page.locator('.ant-card', { hasText: 'Чувствительность тревог' }).first();
  const before = (await slider.innerText()).replace(/\s+/g, ' ');
  check(/Рабочий порог тревоги модели — 0,\d\d\./.test(before), `ползунок: ${before}`);
  await slider.getByRole('slider').focus();
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  const after = (await slider.innerText()).replace(/\s+/g, ' ');
  check(after !== before, `ползунок пересчитывает оценку: ${after}`);
  const channelHref = await page.locator('.ant-table-row a[href*="/channels/"]').first().getAttribute('href');

  await screen('02_objects', '/objects', '.object-tile, .tile, .ant-card');
  await screen('03_channel', channelHref ?? `/channels/${process.env.CHANNEL_ID ?? '231706'}`, 'canvas');
  check((await page.getByText('Данных за период нет').count()) === 0, 'карточка датчика: есть график активности');
  check((await page.getByText('Прогнозов за период нет').count()) === 0, 'карточка датчика: есть история прогнозов');
  const faultsLine = (await page.locator('.ant-space', { hasText: 'Отказы за период по разметке ML' }).last().innerText()).replace(/\s+/g, ' ');
  check(/Неисправен — \d+/.test(faultsLine) && /Пропадание связи — \d+/.test(faultsLine), `карточка датчика: отказы по видам: ${faultsLine}`);
  const factors = await page.locator('.factor-list li').allInnerTexts();
  check(factors.length > 0, `карточка датчика: причины ${JSON.stringify(factors)}`);
  if (process.env.CREATE_WORK_ORDER !== 'false') {
    // Черновик заявки из прогноза — чтобы пройти список и карточку заявки (создаёт запись в БД)
    await page.getByRole('button', { name: 'Создать заявку' }).click();
    await page.getByRole('dialog', { name: 'Черновик заявки на обслуживание' }).getByLabel('Исполнитель').waitFor();
    const created = page.waitForResponse((r) => r.url().includes('/api/work-orders') && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Сохранить черновик' }).click();
    createdWorkOrderId = (await (await created).json()).id;
    await page.getByText(/Черновик заявки .* сохранён/).waitFor();
  }
  await screen('04_journal', '/journal', '.ant-table-row');
  check((await page.locator('.ant-table-row').count()) > 0, 'журнал: есть строки');
  await screen('05_work_orders', '/work-orders', 'h1, h2, h3, h4');
  if ((await page.locator('.ant-table-row a').count()) > 0) {
    await page.locator('.ant-table-row a').first().click();
    await page.getByRole('heading', { name: /^Заявка / }).waitFor();
    await page.waitForLoadState('networkidle');
    await page.screenshot({ path: resolve(outDir, '05b_work_order.png'), fullPage: true });
    const wo = (await page.locator('main, .ant-layout-content').first().innerText()).replace(/\s+/g, ' ');
    console.log(`\n=== 05b_work_order (${page.url().replace(base, '')})\nТекст: ${wo.slice(0, 500)}`);
    check(/Причины/.test(wo) && !/Часов до превышения/.test(wo), 'карточка заявки: понятные причины из прогноза ML');
  }
  await screen('06_quality', '/quality', 'canvas');
  const quality = (await page.locator('main, .ant-layout-content').first().innerText()).replace(/\s+/g, ' ');
  check(quality.includes(`Precision (точность) ${expectPrecision}`), `качество модели: Precision ${expectPrecision} %`);
  check((await page.locator('canvas').count()) >= 2, 'качество модели: графики PR-кривой и «прогноз против факта»');
} finally {
  await browser.close();
  // Тестовую заявку не оставляем: удаляем черновик от имени автора (DELETE разрешён только для черновиков)
  if (createdWorkOrderId) {
    const login = await fetch(`${base}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'dispatcher', password: 'dispatcher123' }),
    });
    const { access_token } = await login.json();
    const del = await fetch(`${base}/api/work-orders/${createdWorkOrderId}`, { method: 'DELETE', headers: { Authorization: `Bearer ${access_token}` } });
    check(del.status === 204, `тестовая заявка ${createdWorkOrderId} удалена (HTTP ${del.status})`);
  }
}
console.log(`\nПроблемы (${problems.length}):\n${problems.join('\n') || 'нет'}`);
process.exit(problems.length ? 1 : 0);
