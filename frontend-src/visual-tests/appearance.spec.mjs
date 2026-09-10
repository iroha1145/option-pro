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

      const trigger = page.getByRole('button', { name: /外观/ }).first();
      await expect.poll(() => trigger.evaluate((el) => getComputedStyle(el).boxShadow)).not.toMatch(/255,\s*255,\s*255/);

      const whiteEdges = await page.evaluate(() => {
        const nearWhite = (value) => {
          if (!value || value === 'none') return false;
          for (const match of value.matchAll(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/g)) {
            const r = Number(match[1]);
            const g = Number(match[2]);
            const b = Number(match[3]);
            const a = match[4] === undefined ? 1 : Number(match[4]);
            if (a > 0.2 && r >= 230 && g >= 230 && b >= 230) return true;
          }
          return false;
        };
        return Array.from(document.querySelectorAll('button, [role="button"], .control-button'))
          .filter((el) => {
            const style = getComputedStyle(el);
            return nearWhite(style.boxShadow)
              || (parseFloat(style.borderTopWidth) > 0 && nearWhite(style.borderTopColor));
          })
          .map((el) => (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 24));
      });
      expect(whiteEdges, `Route ${route} at ${width}px still has near-white button edges`).toEqual([]);
    }
  }
  expect(errors).toEqual([]);
});
