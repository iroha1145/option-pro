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
      const headerBox = await header.first().boundingBox();
      for (const control of [search, login]) {
        const bounds = await control.boundingBox();
        expect(bounds?.width).toBeGreaterThanOrEqual(44);
        expect(bounds?.height).toBeGreaterThanOrEqual(44);
        // 触控区 44px 是透明的；看得见的按钮只画 32px，不顶满 48px 高的手机页头（2026-10-08 用户反馈）。
        const chip = await control.locator('.header-chip').boundingBox();
        expect(chip?.height).toBeLessThanOrEqual(32);
        expect((chip?.y ?? 0) - (headerBox?.y ?? 0)).toBeGreaterThanOrEqual(6);
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

// 行情带在触屏上加高到 44px：按钮整格可点，内容（含基金的涨跌徽标）要在按钮里垂直居中，
// 不能因为基线对齐贴到上沿（2026-10-05 生产手机截图）。
for (const funds of [true, false]) {
  test(`touch tape items are 44px and vertically centred (${funds ? 'funds' : 'indices'})`, async ({ browser }, testInfo) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, reducedMotion: 'reduce' });
    const page = await context.newPage();
    const at = new Date().toISOString();
    const status = { enabled: true, configured: true, allowed: funds, public_enabled: true, connected: true, connection_status: 'connected', market_session: 'closed' };
    await page.addInitScript(() => {
      window.EventSource = class { addEventListener() {} close() {} };
      localStorage.setItem('optix:locale', 'zh');
    });
    await page.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
    await page.route('**/api/**', route => {
      const url = new URL(route.request().url());
      if (!url.pathname.startsWith('/api/')) return route.continue();
      if (url.pathname === '/api/access/status') return route.fulfill({ json: { access_mode: 'password', logged_in: false, account: null } });
      if (url.pathname === '/api/quotes') return route.fulfill({ json: { status, quotes: funds ? (url.searchParams.get('symbols') ?? '').split(',').filter(Boolean).map(symbol => ({
        symbol, price: 281.52, previous_close: 279, change: 2.52, change_pct: 0.9, trade_at: at, received_at: at, source: 'finnhub', session: 'closed', freshness: 'live', subscription_status: 'live',
      })) : [] } });
      if (url.pathname === '/api/market/indices') return route.fulfill({ json: { indices: [{ code: 'SPX', symbol: '^GSPC', price: 7722.72, change_percent: 0.73 }] } });
      return route.fulfill({ status: 503, json: { message: 'fixture unavailable' } });
    });
    await page.goto(`${testInfo.project.use.baseURL}/`);
    const item = page.locator('.marquee-track button').first();
    await expect(item).toBeVisible();
    if (funds) await expect(item).toContainText('+0.90%');
    const gaps = await item.evaluate((button) => {
      const box = button.getBoundingClientRect();
      const rects = [...button.children].filter(child => child.getAttribute('aria-hidden') !== 'true').map(child => child.getBoundingClientRect());
      return { height: box.height, top: Math.min(...rects.map(r => r.top)) - box.top, bottom: box.bottom - Math.max(...rects.map(r => r.bottom)) };
    });
    expect(gaps.height).toBeGreaterThanOrEqual(44);
    expect(Math.abs(gaps.top - gaps.bottom)).toBeLessThanOrEqual(4);
    await page.locator('.marquee-track').screenshot({ path: testInfo.outputPath(`touch-tape-${funds ? 'funds' : 'indices'}.png`) });
    await context.close();
  });
}
