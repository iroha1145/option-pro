import { expect, test } from '@playwright/test';

for (const width of [320, 390]) {
  for (const locale of ['zh', 'en', 'ja']) {
    test(`phone login controls remain usable at ${width}px in ${locale}`, async ({ browser }, testInfo) => {
      const context = await browser.newContext({ viewport: { width, height: 844 }, hasTouch: true, isMobile: true, reducedMotion: 'reduce' });
      const page = await context.newPage();
      let customer = null;
      await page.addInitScript(({ locale, dark }) => {
        localStorage.setItem('optix:locale', locale);
        localStorage.setItem('optix_theme', dark ? 'dark' : 'light');
      }, { locale, dark: width === 320 });
      await page.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (!path.startsWith('/api/')) return route.continue();
        if (path === '/api/access/status') return route.fulfill({ json: { access_mode: 'password', logged_in: false,
          account: customer ? { logged_in: true, username: customer } : null } });
        if (path === '/api/account/watchlist') return route.fulfill({ json: { tickers: [], max_tickers: 50 } });
        if (path === '/api/ai/status') return route.fulfill({ json: { enabled: false } });
        if (path === '/api/runtime-settings') return route.fulfill({ json: { settings: { ai: { manual_analysis_enabled: false } } } });
        return route.fulfill({ status: 503, json: { message: 'fixture unavailable' } });
      });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(`${testInfo.project.use.baseURL}/watchlist`);
      const header = page.locator('header');
      const search = header.locator('button.touch-target:visible').first();
      const login = header.locator('a[href="/login"]');
      await expect(login).toBeVisible();
      for (const control of [search, login]) {
        const bounds = await control.boundingBox();
        expect(bounds?.width).toBeGreaterThanOrEqual(44);
        expect(bounds?.height).toBeGreaterThanOrEqual(44);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      await login.click();
      const password = page.locator('input[type="password"]');
      const reveal = password.locator('..').getByRole('button');
      await expect(reveal).toBeVisible();
      const bounds = await reveal.boundingBox();
      expect(bounds?.width).toBeGreaterThanOrEqual(44);
      expect(bounds?.height).toBeGreaterThanOrEqual(44);
      await password.fill('test-visible-password');
      await reveal.click();
      await expect(page.locator('input[value="test-visible-password"]')).toHaveAttribute('type', 'text');
      await page.screenshot({ path: testInfo.outputPath('phone-login.png'), fullPage: true, animations: 'disabled' });
      customer = 'audit-long-account-name';
      await page.goto(`${testInfo.project.use.baseURL}/watchlist`);
      const logout = page.locator('header button').filter({ hasText: customer });
      await expect(logout).toBeVisible();
      const logoutBounds = await logout.boundingBox();
      expect(logoutBounds?.width).toBeGreaterThanOrEqual(44);
      expect(logoutBounds?.height).toBeGreaterThanOrEqual(44);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      await expect(page.locator('main h1')).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath('phone-account.png'), fullPage: true, animations: 'disabled' });
      expect(errors).toEqual([]);
      await context.close();
    });
  }
}
