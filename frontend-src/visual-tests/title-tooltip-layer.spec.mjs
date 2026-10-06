import { test, expect } from '@playwright/test';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
let bundle;

test.beforeAll(async () => {
  const result = await build({
    stdin: {
      resolveDir: root,
      loader: 'tsx',
      contents: `
        import React, { useState } from 'react';
        import { createRoot } from 'react-dom/client';
        import TitleTooltipLayer from './src/components/shared/TitleTooltipLayer';
        import ThemeSwitcher from './src/components/ThemeSwitcher';
        import { QuoteIndicator } from './src/components/shared/LiveQuote';
        function Fixture() {
          const [at, setAt] = useState('2026-10-05');
          const [mounted, setMounted] = useState(true);
          const [title, setTitle] = useState('原有标题');
          const [description, setDescription] = useState(null);
          const [label, setLabel] = useState('可见名称');
          window.tooltipFixture = { setAt, setMounted, setTitle, setDescription, setLabel };
          return <>
            <ThemeSwitcher />
            {mounted && <div id="quote"><QuoteIndicator symbol="TOOLTIP" usingFallback fallbackAt={at} /></div>}
            <button id="author" title={title} aria-description={description} data-author-description={description ?? 'absent'}>{label}</button>
            <button id="native" title="仅由标题命名"><svg aria-hidden="true" width="12" height="12" /></button>
            <p id="details">作者的关联说明</p>
            <button id="described" title="标题说明" aria-describedby="details">已有说明</button>
            <TitleTooltipLayer />
          </>;
        }
        createRoot(document.querySelector('#fixture')).render(<Fixture />);
      `,
    },
    tsconfig: `${root}tsconfig.app.json`,
    bundle: true,
    format: 'iife',
    jsx: 'automatic',
    write: false,
    define: {
      'process.env.NODE_ENV': '"development"',
      'import.meta.env': '{"DEV":false,"MODE":"test","VITE_API_MODE":"mock"}',
    },
  });
  bundle = result.outputFiles[0].text;
});

test.beforeEach(async ({ page }) => {
  await page.setContent(`
    <style>
      body { min-height: 1600px; padding: 50px; }
      #fixture > * { display: block; margin: 24px 0; }
      [data-title-tooltip] { position: fixed; pointer-events: none; }
    </style>
    <main id="fixture"></main>
  `);
  await page.addScriptTag({ content: bundle });
  await expect(page.locator('#quote > span')).toBeVisible();
});

async function accessibleTitle(page, selector) {
  const session = await page.context().newCDPSession(page);
  try {
    const { root: document } = await session.send('DOM.getDocument');
    const { nodeId } = await session.send('DOM.querySelector', { nodeId: document.nodeId, selector });
    const { node } = await session.send('DOM.describeNode', { nodeId });
    const { nodes } = await session.send('Accessibility.getPartialAXTree', { backendNodeId: node.backendNodeId });
    const target = nodes.find((item) => item.backendDOMNodeId === node.backendNodeId);
    return { name: target?.name?.value ?? '', description: target?.description?.value ?? '' };
  } finally {
    await session.detach();
  }
}

test('hover keeps the real theme button and quote accessibility descriptions', async ({ page }) => {
  const theme = page.getByRole('button', { name: '切换外观', exact: true });
  const before = await accessibleTitle(page, 'button.theme-switcher-control');
  expect(before.description).toBe('当前外观：跟随系统');
  await theme.hover();
  await theme.focus();
  await expect.poll(() => accessibleTitle(page, 'button.theme-switcher-control')).toEqual(before);

  const quote = page.locator('#quote > span');
  const beforeQuote = await accessibleTitle(page, '#quote > span');
  expect(beforeQuote.description).toBe('报价日期 2026-10-05');
  await quote.hover();
  await expect.poll(() => accessibleTitle(page, '#quote > span')).toEqual(beforeQuote);
  await page.mouse.move(10, 10);
  await expect(quote).not.toHaveAttribute('aria-description');
  await expect(quote).toHaveAttribute('title', '报价日期 2026-10-05');
});

test('clearing the real quote date clears the tip and never restores the old date', async ({ page }) => {
  const quote = page.locator('#quote > span');
  await quote.hover();
  await expect(page.locator('[data-title-tooltip]')).toContainText('2026\u201110\u201105');
  await page.evaluate(() => window.tooltipFixture.setAt(null));
  await expect(quote).toHaveText('参考价');
  await expect(page.locator('[data-title-tooltip]')).toHaveCount(0);
  await expect(quote).not.toHaveAttribute('aria-description');
  await page.mouse.move(10, 10);
  await expect(quote).toHaveAttribute('title', '');
  await expect(quote).not.toHaveAttribute('data-title-borrowed');
});

test('a quote date arriving during hover appears without moving the pointer', async ({ page }) => {
  await page.evaluate(() => window.tooltipFixture.setAt(null));
  const quote = page.locator('#quote > span');
  await expect(quote).toHaveAttribute('title', '');
  await quote.hover();
  await expect(quote).toHaveAttribute('data-title-borrowed', '');
  await page.evaluate(() => window.tooltipFixture.setAt('2026-10-06'));
  await expect(page.locator('[data-title-tooltip]')).toContainText('2026\u201110\u201106');
  await page.mouse.move(10, 10);
  await expect(quote).toHaveAttribute('title', '报价日期 2026-10-06');
});

test('Escape keeps a dismissed tip hidden through quote updates until pointer reentry', async ({ page }) => {
  const quote = page.locator('#quote > span');
  await quote.hover();
  await expect(page.locator('[data-title-tooltip]')).toBeVisible();
  await page.keyboard.press('Escape');
  await page.evaluate(() => window.tooltipFixture.setAt('2026-10-06'));
  await expect(page.locator('[data-title-tooltip]')).toHaveCount(0);
  await expect(quote).toHaveAttribute('data-title-borrowed', '报价日期 2026-10-06');
  // Cover the complete show delay: an accidental rearm must not flash later.
  await page.waitForTimeout(650);
  await expect(page.locator('[data-title-tooltip]')).toHaveCount(0);
  await page.mouse.move(10, 10);
  await quote.hover();
  await expect(page.locator('[data-title-tooltip]')).toContainText('2026\u201110\u201106');
});

test('removing a title or unmounting its owner cancels the tip and restores borrowed attributes', async ({ page }) => {
  const owner = page.locator('#author');
  await owner.hover();
  await expect(page.locator('[data-title-tooltip]')).toHaveText('原有标题');
  await page.evaluate(() => window.tooltipFixture.setTitle(undefined));
  await expect(page.locator('[data-title-tooltip]')).toHaveCount(0);
  await expect(owner).not.toHaveAttribute('title');
  await expect(owner).not.toHaveAttribute('aria-description');

  const quote = page.locator('#quote > span');
  await quote.hover();
  await expect(page.locator('[data-title-tooltip]')).toBeVisible();
  const node = await quote.elementHandle();
  await page.evaluate(() => window.tooltipFixture.setMounted(false));
  await expect(page.locator('[data-title-tooltip]')).toHaveCount(0);
  await expect.poll(() => node.evaluate((element) => ({ title: element.title, borrowed: element.getAttribute('data-title-borrowed'), description: element.getAttribute('aria-description') }))).toEqual({ title: '报价日期 2026-10-05', borrowed: null, description: null });
});

test('author descriptions and linked descriptions keep their precedence and ownership', async ({ page }) => {
  await page.evaluate(() => window.tooltipFixture.setDescription('作者说明'));
  const owner = page.locator('#author');
  await owner.hover();
  await expect.poll(() => accessibleTitle(page, '#author')).toEqual({ name: '可见名称', description: '作者说明' });
  await page.evaluate(() => window.tooltipFixture.setDescription('后来更新的说明'));
  await page.mouse.move(10, 10);
  await expect(owner).toHaveAttribute('aria-description', '后来更新的说明');

  const linked = page.locator('#described');
  const before = await accessibleTitle(page, '#described');
  expect(before.description).toBe('作者的关联说明');
  await linked.hover();
  await expect.poll(() => accessibleTitle(page, '#described')).toEqual(before);
  await page.mouse.move(10, 10);
  await expect(linked).not.toHaveAttribute('aria-description');
  await expect(linked).toHaveAttribute('aria-describedby', 'details');
});

test('an author taking ownership of the same description is respected on release', async ({ page }) => {
  const owner = page.locator('#author');
  await owner.hover();
  await expect(owner).toHaveAttribute('aria-description', '原有标题');
  await page.evaluate(() => window.tooltipFixture.setDescription('原有标题'));
  await expect(owner).toHaveAttribute('data-author-description', '原有标题');
  await page.mouse.move(10, 10);
  await expect(owner).toHaveAttribute('aria-description', '原有标题');
});

test('title-only names stay native, including when a hovered control loses its text', async ({ page }) => {
  const native = page.locator('#native');
  const before = await accessibleTitle(page, '#native');
  expect(before.name).toBe('仅由标题命名');
  await native.hover();
  await expect(native).toHaveAttribute('title', '仅由标题命名');
  await expect(native).not.toHaveAttribute('data-title-borrowed');
  await expect.poll(() => accessibleTitle(page, '#native')).toEqual(before);

  const owner = page.locator('#author');
  await owner.hover();
  await expect(owner).toHaveAttribute('data-title-borrowed', '原有标题');
  await page.evaluate(() => window.tooltipFixture.setLabel(''));
  await expect(owner).toHaveAttribute('title', '原有标题');
  await expect(owner).not.toHaveAttribute('data-title-borrowed');
  await expect.poll(() => accessibleTitle(page, '#author')).toEqual({ name: '原有标题', description: '' });
});
