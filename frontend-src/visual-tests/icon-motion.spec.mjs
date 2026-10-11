import { test, expect } from '@playwright/test';

const preview = '/visual-tests/support/icon-motion-preview.html';
const running = (locator) => locator.evaluate(element => element.getAnimations({ subtree: true })
  .filter(animation => animation instanceof CSSAnimation && animation.animationName.startsWith('icon-') && animation.playState === 'running').length);

test('all gestures run once on hover, stay in place, and replay after leaving', async ({ page }) => {
  test.setTimeout(45_000);
  await page.goto(preview);
  const buttons = page.locator('.preview-play');
  await expect(buttons).toHaveCount(14);
  await page.screenshot({ path: '../output/icon-motion/preview-desktop.png' });
  for (const button of await buttons.all()) {
    await button.scrollIntoViewIfNeeded();
    const box = await button.boundingBox();
    await button.hover();
    await expect.poll(() => running(button)).toBeGreaterThan(0);
    await expect.poll(() => running(button)).toBe(0);
    expect(await button.boundingBox()).toEqual(box);
    await page.mouse.move(1, 1);
  }
  await page.getByRole('button', { name: '播放全部一次', exact: true }).click();
  await expect.poll(() => running(page.locator('.preview-grid'))).toBeGreaterThan(10);
  await expect.poll(() => page.locator('.preview-grid [data-motion-active="true"]').count()).toBe(0);
  await page.getByRole('button', { name: '播放搜索动效', exact: true }).hover();
  await expect.poll(() => running(buttons.first())).toBeGreaterThan(0);
});

test('keyboard focus animates; disabled controls and reduced motion remain static', async ({ page }) => {
  await page.goto(preview);
  const search = page.getByRole('button', { name: '播放搜索动效', exact: true });
  await page.getByRole('link', { name: '查看应用试作 ↗', exact: true }).focus();
  await page.keyboard.press('Tab');
  await expect(search).toBeFocused();
  await expect.poll(() => running(search)).toBeGreaterThan(0);
  await page.keyboard.press('Tab');
  // The same real component must honor both native and ARIA disabled parents.
  await search.evaluate(button => { button.disabled = true; });
  await search.hover({ force: true });
  expect(await running(search)).toBe(0);
  await page.mouse.move(1, 1);
  await search.evaluate(button => { button.disabled = false; button.setAttribute('aria-disabled', 'true'); });
  await search.hover();
  expect(await running(search)).toBe(0);
  await search.evaluate(button => button.removeAttribute('aria-disabled'));
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.getByRole('button', { name: '播放全部一次', exact: true }).click();
  expect(await running(page.locator('.preview-grid'))).toBe(0);
  await search.hover();
  expect(await running(search)).toBe(0);
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.mouse.move(1, 1);
  await search.hover();
  await expect.poll(() => running(search)).toBeGreaterThan(0);
});

test('restarting a staggered gesture retains the already finished part', async ({ page }) => {
  await page.goto(preview);
  const candle = page.getByRole('button', { name: '播放行情动效', exact: true });
  await candle.hover();
  await expect.poll(() => running(candle)).toBe(2);
  // Freeze the real race window: one part has ended while its neighbour has
  // not. No guessed timeout is needed to exercise a rapid second gesture.
  await candle.evaluate(button => {
    const animations = button.querySelector('svg').getAnimations({ subtree: true })
      .filter(animation => animation instanceof CSSAnimation);
    animations[0].finish();
    animations[1].pause();
    animations[1].currentTime = 200;
  });
  await page.mouse.down();
  await expect.poll(() => running(candle)).toBe(2);
  await page.mouse.up();
});

test('refresh keeps its current turn when pointer entry, focus and click overlap', async ({ page }) => {
  await page.goto(preview);
  const refresh = page.getByRole('button', { name: '播放刷新动效', exact: true });
  await refresh.hover();
  await expect.poll(() => running(refresh)).toBeGreaterThan(0);
  await refresh.evaluate(button => {
    const svg = button.querySelector('svg');
    svg.setAttribute('data-playback-probe', 'same-node');
    for (const animation of svg.getAnimations({ subtree: true })) animation.currentTime = 400;
  });
  await page.mouse.move(1, 1);
  await refresh.hover();
  const timeAfterReturn = await refresh.evaluate(button => button.querySelector('svg').getAnimations({ subtree: true })
    .filter(animation => animation instanceof CSSAnimation && animation.animationName === 'icon-refresh')
    .map(animation => animation.currentTime));
  expect(timeAfterReturn.length).toBe(2);
  expect(timeAfterReturn.every(time => time >= 400)).toBe(true);
  await refresh.click();
  await expect(refresh.locator('svg')).toHaveAttribute('data-playback-probe', 'same-node');
  await expect.poll(() => running(refresh)).toBe(0);
  await page.mouse.move(1, 1);
  await refresh.hover();
  await expect.poll(() => running(refresh)).toBeGreaterThan(0);
});

test('larger gestures keep their strokes inside the icon canvas throughout the cycle', async ({ page }) => {
  await page.goto(preview);
  const clipped = await page.locator('.preview-play svg').evaluateAll(icons => {
    const failures = [];
    for (const svg of icons) {
      const animations = svg.getAnimations({ subtree: true });
      const duration = Math.max(...animations.map(animation => {
        const timing = animation.effect.getTiming();
        return Number(timing.duration) + Number(timing.delay);
      }));
      for (let step = 0; step <= 20; step++) {
        for (const animation of animations) { animation.pause(); animation.currentTime = duration * step / 20; }
        const inverse = svg.getScreenCTM().inverse();
        for (const shape of svg.querySelectorAll('path, circle, rect')) {
          const matrix = inverse.multiply(shape.getScreenCTM());
          const style = getComputedStyle(shape);
          const scale = Math.max(Math.hypot(matrix.a, matrix.b), Math.hypot(matrix.c, matrix.d));
          const padding = style.stroke === 'none' ? 0 : parseFloat(style.strokeWidth) * scale / 2;
          const length = shape.getTotalLength();
          for (let sample = 0; sample <= 80; sample++) {
            const point = shape.getPointAtLength(length * sample / 80).matrixTransform(matrix);
            if (point.x - padding < -.05 || point.y - padding < -.05 || point.x + padding > 24.05 || point.y + padding > 24.05) {
              failures.push({ icon: svg.dataset.iconMotion, step, x: point.x, y: point.y, padding });
              break;
            }
          }
        }
      }
    }
    return failures;
  });
  expect(clipped).toEqual([]);
});

test('real header search, keyboard settings and narrow navigation keep working', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  const search = page.getByRole('button', { name: '搜索代码或功能', exact: true });
  await search.hover();
  await expect.poll(() => running(search)).toBeGreaterThan(0);
  await search.click();
  await expect(page.getByRole('combobox')).toBeVisible();
  await page.keyboard.press('Escape');
  const settings = page.getByRole('button', { name: '显示设置', exact: true });
  await settings.focus();
  await page.keyboard.press('Enter');
  const menu = page.getByRole('menu', { name: '显示设置', exact: true });
  await expect(menu).toBeVisible();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Escape');
  await expect(settings).toBeFocused();
  await page.setViewportSize({ width: 390, height: 844 });
  const dock = page.getByRole('navigation', { name: '移动端导航', exact: true });
  await expect(dock).toBeVisible();
  await dock.getByRole('link', { name: '我的关注', exact: true }).click();
  await expect(page).toHaveURL(/\/watchlist$/);
  await expect(dock.getByRole('link', { name: '我的关注', exact: true })).toHaveAttribute('aria-current', 'page');
  await dock.getByRole('button', { name: '更多', exact: true }).click();
  await expect(page.getByText('更多功能', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '财报日历', exact: true }).click();
  await expect(page).toHaveURL(/\/earnings$/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test('preview fits phone widths in both themes and static comparison removes motion', async ({ page }) => {
  await page.goto(preview);
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole('button', { name: '深色预览', exact: true }).click();
    await expect(page.locator('html')).toHaveClass(/dark/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `../output/icon-motion/preview-dark-${width}.png`, fullPage: true });
    await page.getByRole('button', { name: '浅色预览', exact: true }).click();
  }
  await page.getByRole('button', { name: '静态对照', exact: true }).click();
  await expect(page.locator('.preview-grid [data-icon-motion]')).toHaveCount(0);
  await page.getByRole('button', { name: '播放全部一次', exact: true }).click();
  expect(await running(page.locator('.preview-grid'))).toBe(0);
});

test('touch feedback does not stick or replay on unrelated taps', async ({ browser }) => {
  const context = await browser.newContext({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 }, reducedMotion: 'no-preference' });
  const page = await context.newPage();
  await page.goto('http://127.0.0.1:3192' + preview);
  const search = page.getByRole('button', { name: '播放搜索动效', exact: true });
  await search.tap();
  await expect.poll(() => running(search)).toBeGreaterThan(0);
  await expect.poll(() => running(search)).toBe(0);
  await page.getByRole('heading', { name: '搜索', exact: true }).tap();
  expect(await running(search)).toBe(0);
  await search.tap();
  await expect.poll(() => running(search)).toBeGreaterThan(0);
  // Repeated taps on an already selected item must replay, too.
  const current = page.getByRole('button', { name: '我的关注', exact: true });
  await current.tap();
  await expect.poll(() => running(current)).toBeGreaterThan(0);
  await expect.poll(() => running(current)).toBe(0);
  await current.tap();
  await expect.poll(() => running(current)).toBeGreaterThan(0);
  // Verify real header buttons after touch release, not only replay cards.
  await page.goto('http://127.0.0.1:3192/');
  const settings = page.getByRole('button', { name: '显示设置', exact: true });
  await settings.tap();
  await expect(page.getByRole('menu', { name: '显示设置', exact: true })).toBeVisible();
  await expect.poll(() => running(settings)).toBeGreaterThan(0);
  await context.close();
});
