import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const harness = '/visual-tests/support/options-harness.html';
const call102 = '查看 看涨（Call） · $102.5 明细';
const put102 = '查看 看跌（Put） · $102.5 明细';
const call105 = '查看 看涨（Call） · $105 明细';

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('optix:locale', 'zh'));
});

async function openHarness(page, width = 1440) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto(harness);
  await expect(page.getByRole('heading', { name: '期权面板隔离测试', exact: true })).toBeVisible();
}

const range = page => page.getByRole('group', { name: '合约范围', exact: true });
const side = page => page.getByRole('group', { name: '合约类型', exact: true });
const table = page => page.getByRole('table', { name: '期权合约列表', exact: true });
const contractRow = (page, name) => table(page).getByRole('row').filter({
  has: page.getByRole('button', { name, exact: true }),
});

test('一行对应一份真实存在的样例合约，行权价保留102.5及缺失字段', async ({ page }) => {
  await openHarness(page);
  await range(page).getByRole('button', { name: '全部合约', exact: true }).click();
  await expect(table(page).getByRole('row')).toHaveCount(7); // One header + six contracts.
  await expect(table(page).getByRole('cell', { name: '看涨（Call）', exact: true })).toHaveCount(4);
  await expect(table(page).getByRole('cell', { name: '看跌（Put）', exact: true })).toHaveCount(2);
  await expect(contractRow(page, call102)).toContainText('$102.5');
  await expect(contractRow(page, put102)).toContainText('$102.5');
  await expect(table(page).getByText('$103', { exact: true })).toHaveCount(0);
  await expect(contractRow(page, call102).getByRole('cell', { name: '300', exact: true })).toBeVisible();
  await expect(contractRow(page, call102)).toContainText('3.0×');
  await expect(contractRow(page, put102)).toContainText('不可比');
  await expect(contractRow(page, put102).getByRole('cell', { name: '0', exact: true })).toBeVisible();
  await expect(contractRow(page, call105).getByRole('cell', { name: '—', exact: true })).toHaveCount(3);
  await expect(table(page).getByRole('button', { name: '查看 看跌（Put） · $100 明细', exact: true })).toHaveCount(0);
});

test('仅看异动包含精确3倍与零持仓合约，按成交量排序并可叠加看涨筛选', async ({ page }) => {
  await openHarness(page);
  const alerts = range(page).getByRole('button', { name: '仅看异动', exact: true });
  await alerts.click();
  await expect(alerts).toHaveAttribute('aria-pressed', 'true');
  const rows = table(page).getByRole('row');
  await expect(rows).toHaveCount(4);
  await expect(rows.nth(1).getByRole('button', { name: '查看 看涨（Call） · $100 明细', exact: true })).toBeVisible();
  await expect(rows.nth(2).getByRole('button', { name: call102, exact: true })).toBeVisible();
  await expect(rows.nth(3).getByRole('button', { name: put102, exact: true })).toBeVisible();
  await expect(page.getByText('按成交量从高到低排列', { exact: true })).toBeVisible();

  const calls = side(page).getByRole('button', { name: '看涨（Call）', exact: true });
  await calls.click();
  await expect(calls).toHaveAttribute('aria-pressed', 'true');
  await expect(rows).toHaveCount(3);
  await expect(table(page).getByRole('cell', { name: '看跌（Put）', exact: true })).toHaveCount(0);
  await expect(table(page).getByRole('button', { name: call102, exact: true })).toBeVisible();

  await range(page).getByRole('button', { name: '全部合约', exact: true }).click();
  await expect(rows).toHaveCount(5);
  await expect(rows.nth(3).getByRole('button', { name: call105, exact: true })).toBeVisible();
  await expect(rows.nth(4).getByRole('button', { name: '查看 看涨（Call） · $110 明细', exact: true })).toBeVisible();
});

test('合约明细显示32%隐波与对应买卖报价，缺失值不变成零', async ({ page }) => {
  await openHarness(page);
  await table(page).getByRole('button', { name: call102, exact: true }).click();
  const detail = page.getByRole('region', { name: '合约报价明细', exact: true });
  await expect(detail.getByRole('heading', { name: '看涨（Call） · $102.5', exact: true })).toBeVisible();
  await expect(detail.getByText('32.0%', { exact: true })).toBeVisible();
  await expect(detail.getByText('$1.20', { exact: true })).toBeVisible();
  await expect(detail.getByText('$1.40', { exact: true })).toBeVisible();
  await expect(detail).toContainText('不是实际资金流入');
  await expect(detail).toContainText('成交量为持仓量的 3.0 倍');
  await detail.getByRole('button', { name: '收起合约明细', exact: true }).click();

  await table(page).getByRole('button', { name: call105, exact: true }).click();
  await expect(detail.getByRole('heading', { name: '看涨（Call） · $105', exact: true })).toBeVisible();
  await expect(detail.getByText('—', { exact: true })).toHaveCount(3);
  await expect(detail.getByText('$2.00', { exact: true })).toBeVisible();
  await expect(detail.getByText('0.0%', { exact: true })).toHaveCount(0);
});

test('键盘打开和关闭明细后焦点回到原合约按钮', async ({ page }) => {
  await openHarness(page);
  const trigger = table(page).getByRole('button', { name: call102, exact: true });
  const detail = page.getByRole('region', { name: '合约报价明细', exact: true });
  await trigger.focus();
  await page.keyboard.press('Enter');
  await expect(detail).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(detail).toBeHidden();
  await expect(trigger).toBeFocused();

  await page.keyboard.press('Enter');
  await expect(detail).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(detail.getByRole('button', { name: '收起合约明细', exact: true })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(detail).toBeHidden();
  await expect(trigger).toBeFocused();
});

test('摘要指出同侧部分合约缺成交量，异动计数包含3倍边界', async ({ page }) => {
  await openHarness(page);
  const summary = page.getByRole('region', { name: '期权成交摘要', exact: true });
  await expect(summary.getByText('看涨期权成交', { exact: true })).toBeVisible();
  await expect(summary.getByText('已知 3/4 份合约 · 数据不完整', { exact: true })).toBeVisible();
  await expect(summary.getByText('来自 2 份已知合约', { exact: true })).toBeVisible();
  await expect(summary.getByText('6.4K', { exact: false })).toBeVisible();
  await expect(summary.getByText('需关注合约', { exact: true })).toBeVisible();
  await expect(summary.getByText('3份', { exact: true })).toBeVisible();
});

test('390px下展示单合约卡片，筛选和报价明细不造成整页横向溢出', async ({ page }) => {
  await openHarness(page, 390);
  const list = page.getByRole('list', { name: '期权合约列表', exact: true });
  await expect(list).toBeVisible();
  await expect(list.getByRole('listitem')).toHaveCount(6);
  await expect(table(page)).toBeHidden();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  await expect(page.getByText('已知 3/4 份合约 · 数据不完整', { exact: true })).toBeVisible();

  await range(page).getByRole('button', { name: '仅看异动', exact: true }).click();
  await side(page).getByRole('button', { name: '看涨（Call）', exact: true }).click();
  await expect(list.getByRole('listitem')).toHaveCount(2);
  const trigger = list.getByRole('button', { name: call102, exact: true });
  await trigger.click();
  const detail = page.getByRole('region', { name: '合约报价明细', exact: true });
  await expect(detail.getByText('32.0%', { exact: true })).toBeVisible();
  await expect(detail.getByText('$1.20', { exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  await mkdir('test-results/review-evidence', { recursive: true });
  await page.screenshot({ path: 'test-results/review-evidence/options-390-details.png', fullPage: true, animations: 'disabled' });
  await detail.getByRole('button', { name: '收起合约明细', exact: true }).click();
  await expect(trigger).toBeFocused();
});

for (const width of [390, 1440]) {
  test(`AAPL个股详情的期权链在${width}px下完成加载、切换到期日并展开实际合约`, async ({ page }) => {
    test.setTimeout(60_000);
    await page.setViewportSize({ width, height: 1000 });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {
      if (message.type() === 'error' && /same key|unique.*key/i.test(message.text())) {
        errors.push(message.text());
      }
    });
    await page.goto('/stock/AAPL');
    await expect(page.getByRole('heading', { level: 1 })).toContainText('AAPL');
    await expect(page.getByText('演示模式 · 当前行情与信号为示例数据', { exact: true })).toBeVisible();

    const title = page.getByRole('heading', { name: '期权链', exact: true });
    await title.scrollIntoViewIfNeeded();
    // StockDetail currently gives the card no accessible region name; scope
    // through its actual heading instead of finding unrelated page tables.
    const panel = title.locator('..');
    const expiration = panel.getByRole('combobox', { name: '选择到期日', exact: true });
    const attention = panel.getByRole('region', { name: '成交关注', exact: true });
    const collection = width < 768
      ? panel.getByRole('list', { name: '期权合约列表', exact: true })
      : panel.getByRole('table', { name: '期权合约列表', exact: true });
    const contractButtons = collection.getByRole('button', { name: /^查看 .+ 明细$/ });
    await expect(expiration).toBeVisible();
    await expect(attention).toBeVisible();
    await expect(collection).toBeVisible();
    await expect(panel.locator('[data-state="loading"]')).toHaveCount(0);
    await expect(contractButtons).toHaveCount(22); // 11 observed strikes × two demo legs.
    await expect(panel.getByText(/^标的价\s*232\.10$/)).toBeVisible();
    if (width < 768) {
      await expect(collection.getByRole('listitem').first().getByText('参考价（每股）', { exact: true })).toBeVisible();
    } else {
      await expect(collection.getByRole('columnheader', { name: '参考价（每股）', exact: true })).toBeVisible();
      await expect(collection.getByRole('columnheader', { name: '成交量', exact: true })).toBeVisible();
    }

    // Give the old expiry distinct UI state and an open contract before moving
    // to a different expiry. A reused old ChainBrowser must fail these checks.
    await range(page).getByRole('button', { name: '全部合约', exact: true }).click();
    await side(page).getByRole('button', { name: '看涨（Call）', exact: true }).click();
    await expect(contractButtons).toHaveCount(16);
    await contractButtons.first().click();
    const detail = panel.getByRole('region', { name: '合约报价明细', exact: true });
    await expect(detail).toBeVisible();
    const oldDate = (await expiration.innerText()).match(/\d{4}-\d{2}-\d{2}/)?.[0];
    expect(oldDate).toBeTruthy();
    await expiration.click();
    const nextOption = page.getByRole('option').last();
    await expect(nextOption).toBeVisible();
    const nextDate = (await nextOption.innerText()).match(/\d{4}-\d{2}-\d{2}/)?.[0];
    expect(nextDate).toBeTruthy();
    expect(nextDate).not.toBe(oldDate);
    await nextOption.click();
    await expect(expiration).toContainText(nextDate);
    await expect(detail).toBeHidden();
    await expect(range(page).getByRole('button', { name: '现价附近', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(side(page).getByRole('button', { name: '全部', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(contractButtons).toHaveCount(22);
    await expect(panel.locator('[data-state="loading"]')).toHaveCount(0);
    await expect(panel.getByText(/^标的价\s*232\.10$/)).toBeVisible();
    await expect(page).toHaveURL(/\/stock\/AAPL$/);

    const trigger = contractButtons.first();
    const selectedName = (await trigger.getAttribute('aria-label')).replace(/^查看 /, '').replace(/ 明细$/, '');
    await trigger.click();
    await expect(detail.getByRole('heading', { name: selectedName, exact: true })).toBeVisible();
    await expect(detail.getByText('买方报价', { exact: true })).toBeVisible();
    await expect(detail.getByText('卖方报价', { exact: true })).toBeVisible();
    await expect(detail.getByText('隐含波动率', { exact: true })).toBeVisible();
    await detail.getByRole('button', { name: '收起合约明细', exact: true }).click();
    await expect(detail).toBeHidden();
    await expect(trigger).toBeFocused();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);

    // Capture the option content, not the K-line chart at the top of the page.
    await attention.evaluate(element => {
      element.scrollIntoView({ block: 'start', behavior: 'instant' });
      window.scrollBy({ top: -90, behavior: 'instant' });
    });
    await expect(attention.getByRole('heading', { name: '成交关注', exact: true })).toBeInViewport();
    await expect(range(page)).toBeInViewport();
    await expect(contractButtons.first()).toBeInViewport();
    await mkdir('test-results/feedback-evidence', { recursive: true });
    await page.screenshot({ path: `test-results/feedback-evidence/options-stock-${width}.png`, animations: 'disabled' });
    expect(errors).toEqual([]);
  });
}
