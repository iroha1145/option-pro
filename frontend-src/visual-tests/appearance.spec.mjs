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

for (const signedIn of [false, true]) {
  test(`top-right control stays usable on a phone viewport (${signedIn ? 'signed in' : 'signed out'})`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    let loggedIn = false;
    await page.route('**/api/access/status', (route) => route.fulfill({
      json: { access_mode: 'password', logged_in: loggedIn },
    }));
    await page.route('**/api/access/login', (route) => {
      loggedIn = true;
      return route.fulfill({ json: { logged_in: true } });
    });
    await page.route('**/api/ai/status', (route) => route.fulfill({ json: { enabled: false } }));
    await page.route('**/api/runtime-settings', (route) => route.fulfill({ json: { settings: { ai: { manual_analysis_enabled: false } } } }));
    const header = page.getByRole('banner');
    const account = signedIn
      ? header.getByRole('button', { name: '退出', exact: true })
      : header.getByRole('link', { name: '登录', exact: true });
    await page.goto('/');
    if (signedIn) {
      // The login UI works in both the mock preview and the live API fixture.
      await header.getByRole('link', { name: '登录', exact: true }).click();
      await page.getByRole('textbox', { name: '用户名', exact: true }).fill('admin');
      await page.getByLabel('密码', { exact: true }).fill('appearance-test-password');
      await page.locator('form').getByRole('button', { name: '登录', exact: true }).click();
    }
    await expect(account).toBeVisible();
    const trigger = header.getByRole('button', { name: /外观/ });
    await expect(trigger).toBeVisible();
    // Login and logout have different widths. Check the actual action row rather
    // than an absolute x coordinate sampled before identity finishes loading.
    await expect(async () => {
      const [box, searchBox, accountBox, headerBox] = await Promise.all([
        trigger.boundingBox(), header.getByRole('button', { name: '搜索', exact: true }).boundingBox(),
        account.boundingBox(), header.boundingBox(),
      ]);
      expect(box).toBeTruthy();
      expect(searchBox).toBeTruthy();
      expect(accountBox).toBeTruthy();
      expect(headerBox).toBeTruthy();
      expect(box.width).toBeGreaterThanOrEqual(36);
      expect(box.height).toBeGreaterThanOrEqual(36);
      expect(box.x).toBeGreaterThanOrEqual(searchBox.x + searchBox.width);
      expect(box.x + box.width).toBeLessThanOrEqual(accountBox.x);
      expect(box.y).toBeGreaterThanOrEqual(headerBox.y);
      expect(box.y + box.height).toBeLessThanOrEqual(headerBox.y + headerBox.height);
      expect(accountBox.x + accountBox.width).toBeLessThanOrEqual(390);
    }).toPass({ timeout: 10_000 });
    await trigger.click();
    await page.getByRole('menuitemradio', { name: '深色' }).click();
    await expect(page.locator('html')).toHaveClass(/dark/);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  });
}

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
      // Real-backend runs share a request bucket. Initial identity confirmation
      // may honor up to 60s of Retry-After before the page can safely mount.
      await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible({ timeout: 75_000 });
      await expect(page.getByRole('button', { name: /外观/ }).first()).toBeVisible();
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);

      const trigger = page.getByRole('button', { name: /外观/ }).first();
      await expect.poll(() => trigger.evaluate((el) => getComputedStyle(el).boxShadow)).not.toMatch(/255,\s*255,\s*255/);

      const focusCandidate = page.locator('[class*="focus-visible:ring-offset-"]:not(:disabled)').first();
      if (await focusCandidate.isVisible()) {
        await page.keyboard.press('Tab');
        await focusCandidate.focus();
      }

      const whiteEdges = await page.evaluate(() => {
        const canvas = document.createElement('canvas');
        const context = canvas.getContext('2d', { willReadFrequently: true });
        const nearWhite = (value) => {
          if (!value || value === 'none') return false;
          context.clearRect(0, 0, 1, 1);
          context.fillStyle = value;
          context.fillRect(0, 0, 1, 1);
          const [r, g, b, a] = context.getImageData(0, 0, 1, 1).data;
          return a > 51 && r >= 230 && g >= 230 && b >= 230;
        };
        const whiteShadow = (value) => {
          // Tailwind rings include a white offset shadow with four zero lengths.
          // It paints nothing; only inspect shadows with a visible footprint.
          return value.split(/,(?![^()]*\))/).some((part) => {
            const color = part.match(/(?:rgba?|color)\([^)]*\)/)?.[0];
            const lengths = part.replace(color ?? '', '').match(/-?[\d.]+px/g) ?? [];
            return lengths.some((length) => parseFloat(length) !== 0) && nearWhite(color);
          });
        };
        return Array.from(document.querySelectorAll('button, [role="button"], .control-button, [class*="ring-offset-"]'))
          .filter((el) => {
            const style = getComputedStyle(el);
            return whiteShadow(style.boxShadow)
              || ['Top', 'Right', 'Bottom', 'Left'].some((side) =>
                parseFloat(style[`border${side}Width`]) > 0 && nearWhite(style[`border${side}Color`]));
          })
          .map((el) => (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 24));
      });
      expect(whiteEdges, `Route ${route} at ${width}px still has near-white button edges`).toEqual([]);
    }
  }
  expect(errors).toEqual([]);
});
