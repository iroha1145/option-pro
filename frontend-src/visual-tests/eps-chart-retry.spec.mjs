import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseStaticJsImports } from '../../scripts/perf/lib/round6_bundle_graph.mjs';

function chartDependencies() {
  const assets = fileURLToPath(new URL('../test-results/eps-chart-build/assets/', import.meta.url));
  const entry = fs.readdirSync(assets).find((name) => /^eps-chart-.*\.js$/.test(name));
  if (!entry) throw new Error('Run this test with playwright.eps-chart.config.mjs against the production build');
  const seen = new Set();
  const visit = (name) => {
    if (seen.has(name)) return;
    seen.add(name);
    for (const dependency of parseStaticJsImports(fs.readFileSync(path.join(assets, name), 'utf8'))) {
      visit(path.basename(dependency));
    }
  };
  visit(entry);
  seen.delete(entry);
  return seen;
}

for (const remount of [false, true]) {
  test(`production chart recovers after two failed loads${remount ? ' and a page remount' : ''}`, async ({ page }) => {
    const errors = [];
    const loaded = new Set();
    const dependencies = chartDependencies();
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('response', (response) => {
      const url = new URL(response.url());
      if (response.ok() && url.pathname.endsWith('.js')) loaded.add(path.basename(url.pathname));
    });
    await page.clock.setFixedTime(new Date('2026-09-12T12:00:00Z'));
    await page.addInitScript(() => localStorage.setItem('optix:locale', 'zh'));
    await page.route('**/*', (route) => (
      ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname)
        ? route.continue() : route.abort()
    ));
    let failChart = true;
    const chartRequests = [];
    await page.route(/\/assets\/eps-chart-[^/?]+\.js(?:\?|$)/, async (route) => {
      chartRequests.push({ url: route.request().url(), failed: failChart,
        unloadedDependencies: [...dependencies].filter((name) => !loaded.has(name)) });
      if (failChart) return route.fulfill({ status: 503, body: 'chart unavailable', contentType: 'text/plain' });
      return route.continue();
    });

    const openChart = async () => {
      await expect(page.getByRole('heading', { name: '财报日历', exact: true })).toBeVisible();
      const all = page.getByRole('tab', { name: /^全部公司/ });
      await all.click();
      const slot = page.locator('[data-eps-chart-slot]');
      await expect(slot).toBeAttached();
      await slot.scrollIntoViewIfNeeded();
      return all;
    };
    await page.goto('/earnings');
    let all = await openChart();
    await expect(page.locator('[data-eps-chart-error]')).toBeVisible();
    expect(chartRequests).toHaveLength(1);
    expect(chartRequests[0].unloadedDependencies).toEqual([]);
    await expect(all).toHaveAttribute('aria-selected', 'true');

    await page.getByRole('button', { name: '重试图表', exact: true }).click();
    await expect.poll(() => chartRequests.length).toBe(2);
    await expect(page.locator('[data-eps-chart-error]')).toBeVisible();
    expect(new URL(chartRequests[1].url).searchParams.get('recover')).toBe('1');
    if (remount) {
      await page.getByRole('link', { name: /首页/ }).first().click();
      await expect(page.getByRole('heading', { name: '首页', exact: true })).toBeVisible();
      await page.getByRole('link', { name: /财报/ }).first().click();
      all = await openChart();
      await expect(page.locator('[data-eps-chart-error]')).toBeVisible();
      expect(chartRequests).toHaveLength(2);
    }
    failChart = false;
    await page.getByRole('button', { name: '重试图表', exact: true }).click();
    await expect(page.locator('[data-eps-chart] canvas')).toHaveCount(1);
    await expect(page.locator('[data-eps-chart]')).toBeVisible();
    await expect(page.locator('[data-eps-chart-error]')).toHaveCount(0);
    await expect(all).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('[aria-label="财报主体"]')).toBeVisible();
    expect(chartRequests).toHaveLength(3);
    expect(new URL(chartRequests[2].url).searchParams.get('recover')).toBe('2');
    expect(chartRequests[2].failed).toBe(false);
    expect(chartRequests.every((row) => row.unloadedDependencies.length === 0)).toBe(true);
    if (remount) {
      await page.getByRole('link', { name: /首页/ }).first().click();
      await expect(page.getByRole('heading', { name: '首页', exact: true })).toBeVisible();
      await page.getByRole('link', { name: /财报/ }).first().click();
      await openChart();
      await expect(page.locator('[data-eps-chart] canvas')).toHaveCount(1);
      await expect(page.locator('[data-eps-chart-error]')).toHaveCount(0);
      expect(chartRequests).toHaveLength(3);
    }
    expect(errors).toEqual([]);
  });
}
