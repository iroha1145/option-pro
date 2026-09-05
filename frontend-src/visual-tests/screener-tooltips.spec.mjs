import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const tips = (page) => page.locator('[data-pointer-tooltip]');
const scores = (page) => page.locator('[data-pointer-tooltip-trigger][aria-label="分项强度"]');
const catalysts = (page) => page.locator('[data-pointer-tooltip-trigger][aria-label^="催化剂"]');
const evidence = 'test-results/screener-tooltips-evidence';
async function open(page, suffix = '') {
  // Product components use deterministic local fixtures; remote images/API calls
  // are unnecessary for this interaction check and must not reach live services.
  await page.route('**/*', (route) => {
    const url = new URL(route.request().url());
    return ['127.0.0.1', 'localhost'].includes(url.hostname) ? route.continue() : route.abort();
  });
  await page.goto(`/visual-tests/support/screener-tooltips.html${suffix}`);
  await expect(scores(page).first()).toBeVisible();
}
async function shown(page) { await expect(tips(page)).toHaveCount(1); await expect(tips(page)).toBeVisible(); }
async function bounds(page) {
  const box = await tips(page).boundingBox();
  const viewport = page.viewportSize();
  expect(box.x).toBeGreaterThanOrEqual(7.5); expect(box.y).toBeGreaterThanOrEqual(7.5);
  expect(box.x + box.width).toBeLessThanOrEqual(viewport.width - 7.5);
  expect(box.y + box.height).toBeLessThanOrEqual(viewport.height - 7.5);
  return box;
}
async function capture(page, name) { await mkdir(evidence, {recursive:true}); await page.screenshot({path:`${evidence}/${name}.png`,animations:'disabled'}); }

test('hovering a real result row does not open both cells; each cell owns only its own details', async ({page}) => {
  await open(page);
  await page.getByText('AAA',{exact:true}).hover();
  await expect(tips(page)).toHaveCount(0);
  await scores(page).first().hover(); await shown(page);
  await expect(tips(page)).toContainText('短期'); await expect(tips(page)).toContainText('长期—');
  await expect(tips(page)).not.toContainText('72h');
  await catalysts(page).first().hover(); await shown(page);
  await expect(tips(page)).toContainText('72h 窗口');
  await expect(tips(page)).toContainText('发布新的产品');
  await expect(tips(page)).not.toContainText('突破质量');
  await capture(page,'table-single-catalyst');
});

test('tooltip tracks movement within a cell and closes immediately on pointer exit', async ({page}) => {
  await open(page);
  const box = await scores(page).first().boundingBox();
  await page.mouse.move(box.x + 4, box.y + 5); await shown(page);
  const start = await tips(page).boundingBox();
  await page.mouse.move(box.x + 27, box.y + 11);
  await expect.poll(async () => (await tips(page).boundingBox()).x - start.x).toBeCloseTo(23, 0);
  await expect.poll(async () => (await tips(page).boundingBox()).y - start.y).toBeCloseTo(6, 0);
  await page.mouse.move(4, 4); await expect(tips(page)).toHaveCount(0);
});

test('rapid moves across both tooltip types never leave an exiting tooltip behind', async ({page}) => {
  await open(page);
  await page.evaluate(() => {
    window.maximumPointerTips = 0;
    new MutationObserver(() => { window.maximumPointerTips = Math.max(window.maximumPointerTips, document.querySelectorAll('[data-pointer-tooltip]').length); })
      .observe(document.body,{subtree:true,childList:true});
  });
  for (let index=0; index<18; index++) {
    const target = index % 2 ? catalysts(page).nth(index % 3) : scores(page).nth(index % 3);
    await target.hover(); await shown(page);
    await expect(target).toHaveAttribute('aria-describedby', await tips(page).getAttribute('id'));
    await expect.poll(() => page.evaluate(() => window.maximumPointerTips)).toBeLessThanOrEqual(1);
  }
  await page.mouse.move(4,4); await expect(tips(page)).toHaveCount(0);
  expect(await page.evaluate(() => window.maximumPointerTips)).toBe(1);
});

for (const width of [390,1440]) {
  test(`tooltip flips and stays inside all viewport edges at ${width}px`, async ({page}) => {
    await page.setViewportSize({width,height:420}); await open(page,'?mode=edges');
    for (const corner of ['top-left','top-right','bottom-left','bottom-right']) {
      const group = page.locator(`[data-corner="${corner}"]`);
      for (const target of [group.getByRole('button',{name:'分项强度'}),group.getByRole('button',{name:/催化剂/})]) {
        await target.hover(); await shown(page);
        const box = await bounds(page), trigger = await target.boundingBox();
        if (corner.startsWith('top')) expect(box.y).toBeGreaterThan(trigger.y + trigger.height / 2);
        else expect(box.y + box.height).toBeLessThan(trigger.y + trigger.height / 2);
      }
    }
    await capture(page,`edges-${width}`);
  });
}

test('nested scrolling, page scrolling and resize dismiss stale tooltip coordinates', async ({page}) => {
  await open(page); await scores(page).first().hover(); await shown(page);
  await page.locator('#table-scroll').evaluate((element) => {element.scrollTop=130;});
  await expect(tips(page)).toHaveCount(0);
  await page.locator('#table-scroll').evaluate((element) => {element.scrollTop=0;});
  await catalysts(page).first().hover(); await shown(page);
  await page.evaluate(() => window.scrollTo(0,100)); await expect(tips(page)).toHaveCount(0);
  await page.evaluate(() => window.scrollTo(0,0));
  await scores(page).first().hover(); await shown(page);
  await page.setViewportSize({width:1100,height:750}); await expect(tips(page)).toHaveCount(0);
});

test('keyboard focus exposes one description; Escape and activation do not expand the row', async ({page}) => {
  await open(page);
  const score = scores(page).first(), catalyst = catalysts(page).first();
  await score.focus(); await shown(page);
  await expect(score).toHaveAttribute('aria-describedby',await tips(page).getAttribute('id'));
  await page.keyboard.press('Escape'); await expect(tips(page)).toHaveCount(0); await expect(score).toBeFocused();
  await page.keyboard.press('Enter'); await shown(page);
  await page.keyboard.press('Space'); await expect(tips(page)).toHaveCount(0);
  await catalyst.focus(); await shown(page); await expect(score).not.toHaveAttribute('aria-describedby');
  await page.keyboard.press('Tab'); await expect(tips(page)).toHaveCount(0);
  await expect(page.locator('#row-toggle-count')).toHaveText('0');
});

test('Escape remains dismissed during movement in the same trigger, and reentry opens it again', async ({page}) => {
  await open(page);
  const box=await scores(page).first().boundingBox();
  await page.mouse.move(box.x+5,box.y+8); await shown(page);
  await page.keyboard.press('Escape'); await expect(tips(page)).toHaveCount(0);
  await page.mouse.move(box.x+30,box.y+10); await expect(tips(page)).toHaveCount(0);
  await page.mouse.move(4,4); await scores(page).first().hover(); await shown(page);
});

test('unmounting the active result removes its portal and allows fresh results to open', async ({page}) => {
  await open(page); await scores(page).first().hover(); await shown(page);
  await page.evaluate(() => window.tooltipHarness.setVisible(false)); await expect(tips(page)).toHaveCount(0);
  await page.evaluate(() => window.tooltipHarness.setVisible(true)); await expect(scores(page).first()).toBeVisible();
  await catalysts(page).first().hover(); await shown(page);
});

test.describe('mobile card touch interaction', () => {
  test.use({viewport:{width:390,height:844},hasTouch:true,isMobile:true});
  test('touch shows anchored details, replaces them on another badge, and closes outside without expanding cards', async ({page}) => {
    await open(page,'?mode=cards');
    await scores(page).first().tap(); await shown(page); await bounds(page);
    await catalysts(page).first().tap(); await shown(page); await expect(tips(page)).toContainText('72h 窗口');
    await bounds(page); await capture(page,'mobile-card-touch');
    await catalysts(page).first().tap(); await expect(tips(page)).toHaveCount(0);
    await scores(page).first().tap(); await shown(page);
    await page.locator('#outside').tap(); await expect(tips(page)).toHaveCount(0);
    await expect(page.locator('#row-toggle-count')).toHaveText('0');
  });
});


test('normal motion also swaps the tooltip without exit overlap or position animation', async ({page}) => {
  await page.emulateMedia({reducedMotion:'no-preference'}); await open(page);
  await scores(page).first().hover(); await shown(page);
  await catalysts(page).first().hover(); await shown(page);
  await expect(tips(page)).toContainText('72h 窗口');
  expect(await tips(page).evaluate(el => getComputedStyle(el).transitionProperty)).toBe('none');
  await page.mouse.move(4,4); await expect(tips(page)).toHaveCount(0);
});
