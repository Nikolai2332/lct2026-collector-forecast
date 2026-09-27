/**
 * Проход по всем экранам под каждой ролью (ролевая модель заказчика, docs/SECURITY.md):
 *   node scripts/roles-pass.mjs <адрес стека> <папка скриншотов> [момент at] [id датчика для карточки]
 * Для каждой роли — вход через кнопку быстрого выбора (demo), все экраны на 1920×1080 и 1366×768, проверки:
 * нет ошибок в консоли, нет горизонтальной прокрутки, в шапке — роль и область, пункты меню и кнопки по правам.
 * Скриншоты реальных данных заказчика — только в backups/ (вне git); docs/screenshots — только демо-стек.
 * Нужен браузер: npx playwright install chromium
 */
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const base = (process.argv[2] ?? 'http://localhost:8080').replace(/\/$/, '');
const outDir = resolve(process.argv[3] ?? 'roles-pass');
const AT = process.argv[4] ?? '2026-08-01T12:00:00';

const ROLES = [
  { button: 'Диспетчер ОДС', dir: 'dispatcher_ods', line: 'Диспетчер ОДС · все объекты', users: false, act: true },
  { button: 'Диспетчер района', dir: 'dispatcher', line: 'Диспетчер района · ', users: false, act: true },
  { button: 'Техник', dir: 'technician', line: 'Техник · ', users: false, act: false },
  { button: 'Руководитель', dir: 'manager', line: 'Руководитель · ', users: false, act: false },
  { button: 'Инженер данных', dir: 'engineer', line: 'Инженер данных · все объекты', users: false, act: true },
  { button: 'Администратор', dir: 'admin', line: 'Администратор · все объекты', users: true, act: true },
];
const VIEWPORTS = [
  { width: 1920, height: 1080, sub: '' },
  { width: 1366, height: 768, sub: '1366x768' },
];

const problems = [];
const browser = await chromium.launch();

async function shoot(page, dir, name) {
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(600);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (overflow > 0) problems.push(`${dir}/${name}: горизонтальная прокрутка ${overflow}px`);
  await page.screenshot({ path: resolve(dir, `${name}.png`) });
}

try {
  for (const role of ROLES) {
    for (const vp of VIEWPORTS) {
      const dir = resolve(outDir, role.dir, vp.sub);
      await mkdir(dir, { recursive: true });
      const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, locale: 'ru-RU' });
      const page = await ctx.newPage();
      const tag = `[${role.dir} ${vp.width}]`;
      page.on('console', (m) => m.type() === 'error' && problems.push(`${tag} console: ${m.text().slice(0, 200)}`));
      page.on('pageerror', (e) => problems.push(`${tag} pageerror: ${e.message}`));
      const go = async (path, waitFor) => {
        await page.goto(base + path);
        if (waitFor) await page.locator(waitFor).first().waitFor({ timeout: 60_000 });
      };

      await go('/login', 'form');
      await page.getByRole('button', { name: role.button, exact: true }).click();
      await page.getByRole('heading', { name: 'Дашборд' }).waitFor({ timeout: 60_000 });
      const line = await page.locator('.app-user-role').innerText();
      if (!line.startsWith(role.line)) problems.push(`${tag} шапка: «${line}», ожидалось начало «${role.line}»`);

      await go(`/dashboard?at=${AT}`, '.ant-table-row');
      await shoot(page, dir, '01_dashboard');
      await go(`/objects?at=${AT}`, '.ant-card');
      await shoot(page, dir, '02_objects_tiles');
      await go(`/objects?at=${AT}&view=scheme`, 'canvas');
      await page.waitForTimeout(800);
      await shoot(page, dir, '03_objects_scheme');
      // Карточка первого датчика из топа дашборда — у каждой роли свой (в своей области)
      await go(`/dashboard?at=${AT}`, '.ant-table-row a[href*="/channels/"]');
      await page.locator('.ant-table-row a[href*="/channels/"]').first().click();
      await page.locator('canvas').first().waitFor({ timeout: 60_000 });
      await shoot(page, dir, '04_channel');
      const decide = await page.getByRole('button', { name: 'Отметить решение' }).count();
      if (Boolean(decide) !== role.act) problems.push(`${tag} кнопка «Отметить решение»: ${decide ? 'есть' : 'нет'}`);
      await go(`/journal?at=${AT}`, '.ant-card');
      await shoot(page, dir, '05_journal');
      if (await page.getByRole('tab', { name: 'События' }).count()) {
        await page.getByRole('tab', { name: 'События' }).click();
        await page.locator('.ant-table-row, [data-state="empty"]').first().waitFor({ timeout: 60_000 });
        await shoot(page, dir, '05b_events');
      }
      await go(`/work-orders?at=${AT}`, '.ant-card');
      await shoot(page, dir, '06_work_orders');
      await go(`/quality?at=${AT}`, 'canvas');
      await shoot(page, dir, '07_quality');
      await page.getByRole('button', { name: 'Меню пользователя' }).click();
      const usersItem = await page.getByRole('menuitem', { name: 'Пользователи и роли' }).count();
      if (Boolean(usersItem) !== role.users) problems.push(`${tag} пункт «Пользователи и роли»: ${usersItem ? 'есть' : 'нет'}`);
      await page.keyboard.press('Escape');
      if (role.users) {
        await go('/admin/users', '.ant-table-row');
        await shoot(page, dir, '08_users');
      }
      await ctx.close();
      console.log(`  ✓ ${role.dir} ${vp.width}×${vp.height}`);
    }
  }
} finally {
  await browser.close();
}

if (problems.length) {
  console.error('\nПроблемы:\n' + problems.join('\n'));
  process.exit(1);
}
console.log(`\nГотово: ${ROLES.length} ролей × ${VIEWPORTS.length} экрана, ошибок в консоли и прокрутки нет → ${outDir}`);
