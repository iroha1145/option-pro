import { expect, test } from '@playwright/test';

const routes = [
  '/',
  '/watchlist',
  '/screener',
  '/breakouts',
  '/sectors',
  '/earnings',
  '/catalysts',
  '/market',
  '/cta',
  '/stock/AAPL',
  '/login',
];

async function openAppearanceMenu(page) {
  const trigger = page.getByRole('button', { name: /外观/ }).first();
  await expect(trigger).toBeVisible();
  await trigger.click();
  await expect(page.getByRole('menu', { name: '外观' })).toBeVisible();
  return trigger;
}

test('default follows the device color scheme', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/');
  await expect(page.locator('html')).toHaveClass(/dark/);
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');

  await page.emulateMedia({ colorScheme: 'light' });
  await page.reload();
  await expect(page.locator('html')).not.toHaveClass(/dark/);
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('manual dark and light override the device scheme and persist', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/');
  await openAppearanceMenu(page);
  await page.getByRole('menuitemradio', { name: '深色' }).click();
  await expect(page.locator('html')).toHaveClass(/dark/);
  await expect.poll(() => page.evaluate(() => localStorage.getItem('optix_theme'))).toBe('dark');

  await page.emulateMedia({ colorScheme: 'light' });
  await page.reload();
  await expect(page.locator('html')).toHaveClass(/dark/);

  await openAppearanceMenu(page);
  await page.getByRole('menuitemradio', { name: '浅色' }).click();
  await expect(page.locator('html')).not.toHaveClass(/dark/);
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.reload();
  await expect(page.locator('html')).not.toHaveClass(/dark/);

  await openAppearanceMenu(page);
  await page.getByRole('menuitemradio', { name: '跟随系统' }).click();
  await expect(page.locator('html')).toHaveClass(/dark/);
});

test('appearance menu is keyboard operable and returns focus', async ({ page }) => {
  await page.goto('/');
  const trigger = page.getByRole('button', { name: /外观/ }).first();
  await trigger.focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('menu', { name: '外观' })).toBeVisible();
  await expect(page.getByRole('menuitemradio', { name: '跟随系统' })).toBeFocused();
  await page.keyboard.press('End');
  await expect(page.getByRole('menuitemradio', { name: '深色' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('html')).toHaveClass(/dark/);
  await expect(trigger).toBeFocused();

  // Save screenshot of menu in dark mode
  await openAppearanceMenu(page);
  await page.screenshot({ path: '/opt/cursor/artifacts/screenshots/appearance_menu_dark_desktop.png' });
  await page.keyboard.press('Escape');
});

test('top-right control stays usable on a phone viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  const trigger = page.getByRole('button', { name: /外观/ }).first();
  await expect(trigger).toBeVisible();
  const box = await trigger.boundingBox();
  expect(box).toBeTruthy();
  expect(box.width).toBeGreaterThanOrEqual(36);
  expect(box.height).toBeGreaterThanOrEqual(36);
  expect(box.x + box.width).toBeGreaterThan(300);
  await trigger.click();
  await page.getByRole('menuitemradio', { name: '深色' }).click();
  await expect(page.locator('html')).toHaveClass(/dark/);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
});

test('login page exposes the same control', async ({ page }) => {
  await page.goto('/login');
  await openAppearanceMenu(page);
  await page.getByRole('menuitemradio', { name: '深色' }).click();
  await expect(page.locator('html')).toHaveClass(/dark/);
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

  // Save screenshot of login page in dark mode
  await page.screenshot({ path: '/opt/cursor/artifacts/screenshots/login_dark_no_white_borders.png' });
});

test('research pages stay usable in dark mode on desktop and phone', async ({ page }) => {
  test.setTimeout(180_000);
  await page.addInitScript(() => {
    localStorage.setItem('optix_theme', 'dark');
  });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const route of routes) {
      await page.goto(route);
      await expect(page.locator('html')).toHaveClass(/dark/);
      await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
      await expect(page.getByRole('button', { name: /外观/ }).first()).toBeVisible();
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);

      if (width === 1440 && route === '/') {
        await page.screenshot({ path: '/opt/cursor/artifacts/screenshots/home_dark_no_white_borders.png' });
      } else if (width === 1440 && route === '/screener') {
        await page.screenshot({ path: '/opt/cursor/artifacts/screenshots/screener_dark_no_white_borders.png' });
      }

      // Audit buttons for white borders, light outline, light ring, or light inset shadows in dark mode
      const buttonIssues = await page.evaluate(() => {
        const issues = [];
        const isLight = (str) => {
          if (!str || str === 'none' || str === 'transparent') return false;
          const match = str.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
          if (match) {
            const r = parseInt(match[1], 10);
            const g = parseInt(match[2], 10);
            const b = parseInt(match[3], 10);
            const a = match[4] !== undefined ? parseFloat(match[4]) : 1;
            if (a > 0.1 && (r + g + b) / 3 > 180) return true;
          }
          return false;
        };

        const btns = Array.from(document.querySelectorAll('button, [role="button"], [role="tab"], a[class*="shadow-btn"], .control-button'));
        for (const el of btns) {
          const style = window.getComputedStyle(el);
          const shadow = style.boxShadow;
          const borderTop = style.borderTopColor;
          const borderRight = style.borderRightColor;
          const borderBottom = style.borderBottomColor;
          const borderLeft = style.borderLeftColor;
          const text = (el.textContent || '').trim().slice(0, 20);

          if (shadow && shadow.includes('255, 255, 255')) {
            issues.push({ text, type: 'shadow', value: shadow, cls: el.className });
          }
          if (parseFloat(style.borderTopWidth) > 0 && isLight(borderTop)) {
            issues.push({ text, type: 'border-top', value: borderTop, cls: el.className });
          }
          if (parseFloat(style.borderRightWidth) > 0 && isLight(borderRight)) {
            issues.push({ text, type: 'border-right', value: borderRight, cls: el.className });
          }
          if (parseFloat(style.borderBottomWidth) > 0 && isLight(borderBottom)) {
            issues.push({ text, type: 'border-bottom', value: borderBottom, cls: el.className });
          }
          if (parseFloat(style.borderLeftWidth) > 0 && isLight(borderLeft)) {
            issues.push({ text, type: 'border-left', value: borderLeft, cls: el.className });
          }
        }
        return issues;
      });
      expect(buttonIssues, `Route ${route} at ${width}px has button border/shadow issues`).toEqual([]);
    }
  }
  expect(errors).toEqual([]);
});
