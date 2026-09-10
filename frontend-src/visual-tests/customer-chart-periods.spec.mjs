import { expect, test } from '@playwright/test';

const headers = { Origin: 'https://127.0.0.1:3076', 'X-Optix-Action': '1' };
const periods = [['5m', '5分', 160], ['15m', '15分', 128], ['1h', '1小时', 96], ['1w', '周线', 80]];

for (const width of [1440, 390]) {
  test(`ordinary signed-in customer can open all five periods at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.request.post('/test/reset');
    const registered = await page.request.post('/api/account/register', {
      headers, data: { username: `chartuser${width}`, password: 'fixture-customer-password' },
    });
    expect(registered.status()).toBe(201);
    const access = await (await page.request.get('/api/access/status')).json();
    expect(access.logged_in).toBe(false);
    expect(access.account.logged_in).toBe(true);
    await page.goto('/stock/NVDA');
    await expect(page.getByRole('img', { name: 'NVDA 1d K 线图', exact: true })).toBeVisible();
    for (const [range, label, count] of periods) {
      await page.getByRole('tab', { name: label, exact: true }).click();
      await expect(page.getByRole('img', { name: `NVDA ${range} K 线图`, exact: true })).toBeVisible();
      await expect(page.getByText(new RegExp(`共\\s*${count}\\s*根`))).toBeVisible();
    }
    const before = await (await page.request.get('/test/state')).json();
    expect(before.provider_calls.map((call) => call.range).sort()).toEqual(['15m', '1h', '1w', '5m']);
    await page.request.post('/test/restart-cache');
    await page.reload();
    await page.getByRole('tab', { name: '5分', exact: true }).click();
    await expect(page.getByRole('img', { name: 'NVDA 5m K 线图', exact: true })).toBeVisible();
    expect((await (await page.request.get('/test/state')).json()).provider_calls).toHaveLength(4);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole('img', { name: 'NVDA 5m K 线图', exact: true }).screenshot({ path: info.outputPath(`customer-5m-${width}.png`) });
    await page.goto('/stock/AAPL');
    await page.getByRole('tab', { name: '15分', exact: true }).click();
    await expect(page.getByRole('img', { name: 'AAPL 15m K 线图', exact: true })).toBeVisible();
    expect((await (await page.request.get('/test/state')).json()).provider_calls.at(-1)).toEqual({ ticker: 'AAPL', range: '15m', adjustment: 'raw' });
  });
}

test('an anonymous chart view never starts a provider pull', async ({ page }) => {
  await page.request.post('/test/reset');
  await page.goto('/stock/NVDA');
  await expect(page.getByRole('img', { name: 'NVDA 1d K 线图', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: '5分', exact: true }).click();
  await expect(page.getByText('K 线暂不可用', { exact: true })).toBeVisible();
  await expect(page.getByText(/该股票暂无数据，可手动获取最新行情、日线/)).toHaveCount(0);
  expect((await (await page.request.get('/test/state')).json()).provider_calls).toEqual([]);
});
