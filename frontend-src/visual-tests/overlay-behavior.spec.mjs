import { expect, test } from '@playwright/test';

const drawer = (page) => page.getByRole('dialog', { name: '测试详情' });
const palette = (page) => page.getByRole('dialog', { name: '命令面板' });
const overflow = (page) => page.evaluate(() => ({ value: document.body.style.getPropertyValue('overflow'), priority: document.body.style.getPropertyPriority('overflow') }));
const activeId = (page) => page.evaluate(() => document.activeElement?.id);
async function harness(page) { await page.goto('/visual-tests/support/overlay-harness.html'); await expect(page.locator('#drawer-trigger')).toBeVisible(); }
async function openDrawer(page) { await page.locator('#drawer-trigger').click(); await expect(drawer(page)).toBeVisible(); }
async function settled(page) { await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))); }

test('stacked real dialogs preserve the original overflow and only Escape the top layer', async ({ page }) => {
  await harness(page);
  await page.evaluate(() => document.body.style.setProperty('overflow', 'scroll', 'important'));
  await openDrawer(page);
  await drawer(page).getByText('抽屉内打开命令', { exact: true }).click();
  await expect(palette(page).getByRole('combobox')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(palette(page)).toBeHidden();
  await expect(drawer(page)).toBeVisible();
  await expect.poll(() => activeId(page)).toBe('open-command');
  await expect.poll(() => overflow(page)).toEqual({ value: 'hidden', priority: 'important' });
  await page.keyboard.press('Escape');
  await expect(drawer(page)).toBeHidden();
  await expect.poll(() => overflow(page)).toEqual({ value: 'scroll', priority: 'important' });
  await expect(page.locator('#drawer-trigger')).toBeFocused();
  await expect(page.locator('#already-inert')).toHaveAttribute('inert', '');
  await expect(page.locator('#background')).not.toHaveAttribute('inert');
});

test('closing a lower layer first neither unlocks scrolling nor steals upper focus', async ({ page }) => {
  await harness(page); await openDrawer(page);
  await page.locator('#open-command').click();
  await page.evaluate(() => window.overlayHarness.drawer(false));
  await expect(palette(page).getByRole('combobox')).toBeFocused();
  await expect.poll(() => overflow(page)).toEqual({ value: 'hidden', priority: 'important' });
  await page.keyboard.press('Escape');
  await expect.poll(() => overflow(page)).toEqual({ value: '', priority: '' });
  await expect.poll(() => page.evaluate(() => document.activeElement?.isConnected)).toBe(true);
});

test('modal isolation blocks background focus and Tab includes fixed controls but skips hidden or disabled ancestors', async ({ page }) => {
  await harness(page); await openDrawer(page);
  await page.locator('#background-button').evaluate((button) => button.focus());
  await expect.poll(() => page.evaluate(() => Boolean(document.activeElement?.closest('[role="dialog"]')))).toBe(true);
  const visited = new Set();
  for (let i = 0; i < 16; i++) { visited.add(await activeId(page)); await page.keyboard.press('Tab'); }
  expect(visited.has('fixed-focusable')).toBe(true);
  expect(visited.has('fieldset-disabled')).toBe(false);
  expect(visited.has('ancestor-hidden')).toBe(false);
  expect(visited.has('background-button')).toBe(false);
  await page.locator('#fixed-focusable').focus();
  await page.keyboard.press('Tab');
  await expect(drawer(page).getByRole('button', { name: '关闭抽屉' })).toBeFocused();
});

test('Radix portal selection, Escape and notification dismissal remain usable inside Drawer', async ({ page }) => {
  await harness(page); await openDrawer(page);
  await drawer(page).getByText('显示通知', { exact: true }).click();
  const notice = page.getByRole('alert').filter({ hasText: '测试错误通知' });
  await expect(notice).toBeVisible();
  await drawer(page).getByRole('combobox', { name: '抽屉选择' }).click();
  const options = page.getByRole('listbox');
  await expect(options).toBeVisible();
  await expect.poll(() => options.evaluate((element) => !element.closest('[inert]'))).toBe(true);
  await expect.poll(() => notice.evaluate((element) => !element.closest('[inert],[aria-hidden="true"]'))).toBe(true);
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('option', { name: '前十项', exact: true })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(drawer(page).getByLabel('选择结果')).toHaveText('10');
  await expect(drawer(page).getByRole('combobox', { name: '抽屉选择' })).toBeFocused();
  await drawer(page).getByRole('combobox', { name: '抽屉选择' }).click();
  await page.keyboard.press('Escape');
  await expect(options).toBeHidden();
  await expect(drawer(page)).toBeVisible();
  await notice.getByRole('button').click();
  await expect(notice).toBeHidden();
});

test('tooltip portal remains exposed and top backdrop still closes Drawer', async ({ page }) => {
  await harness(page); await openDrawer(page);
  await drawer(page).getByRole('button', { name: '测试说明：查看说明' }).focus();
  const tooltip = page.getByRole('tooltip');
  await expect(tooltip).toBeVisible();
  await expect.poll(() => tooltip.evaluate((element) => !element.closest('[inert]'))).toBe(true);
  await page.locator('[data-focus-backdrop]').first().click({ position: { x: 10, y: 80 } });
  await expect(drawer(page)).toBeHidden();
  await expect.poll(() => overflow(page)).toEqual({ value: '', priority: '' });
});

test('a removed trigger is never restored and a later overlay still restores its own trigger', async ({ page }) => {
  await harness(page); await openDrawer(page);
  await page.evaluate(() => window.overlayHarness.trigger(false));
  await page.keyboard.press('Escape');
  await expect(drawer(page)).toBeHidden();
  await page.locator('#palette-trigger').click();
  await page.keyboard.press('Escape');
  await expect(page.locator('#palette-trigger')).toBeFocused();
});

test('DrawingWorkspace, LayerMenu and nested confirmation keep one scroll and focus owner', async ({ page }) => {
  await harness(page);
  await page.getByRole('button', { name: '打开绘图工作区', exact: true }).click();
  const workspace = page.getByRole('dialog', { name: '绘图工作区', exact: true });
  await expect(workspace).toBeVisible();
  await page.evaluate(() => window.overlayHarness.layers(true));
  const layers = page.getByRole('dialog', { name: '算法与图层', exact: true });
  await expect(layers).toBeVisible();
  await page.keyboard.press('Tab');
  await expect.poll(() => layers.evaluate((el) => el.contains(document.activeElement))).toBe(true);
  await page.keyboard.press('Escape');
  await expect(layers).toBeHidden();
  await expect(workspace).toBeVisible();
  await expect.poll(() => overflow(page)).toEqual({ value: 'hidden', priority: 'important' });
  await page.evaluate(() => window.overlayHarness.confirm(true));
  await expect(page.getByRole('alertdialog', { name: '确认测试' }).getByRole('button', { name: '取消' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(workspace).toBeVisible();
  await page.evaluate(() => window.overlayHarness.workspace(false));
  await expect.poll(() => overflow(page)).toEqual({ value: '', priority: '' });
});

test('closed palette cancels pending debounce and ignores an already started reply', async ({ page }) => {
  await harness(page); await page.locator('#palette-trigger').click();
  const input = palette(page).getByRole('combobox');
  await input.fill('cancel-before-start');
  await page.keyboard.press('Escape');
  await expect(palette(page)).toBeHidden();
  await page.locator('#palette-trigger').click();
  await input.fill('started');
  await expect.poll(() => page.evaluate(() => window.overlayHarness.searchCalls)).toEqual(['started']);
  await page.keyboard.press('Escape');
  await page.evaluate(() => window.overlayHarness.reply('started', [{ticker:'LATE',name:'Late response',sector:'ignored'}]));
  await expect(palette(page)).toBeHidden();
  await page.locator('#palette-trigger').click();
  await expect(input).toHaveValue('');
  await expect(palette(page).getByText('LATE', { exact: true })).toHaveCount(0);
});

test('palette IME Enter/Escape does not dismiss and short result batches stay keyboard accessible', async ({ page }) => {
  await harness(page); await page.locator('#palette-trigger').click();
  const input = palette(page).getByRole('combobox');
  await input.fill('long');
  await expect.poll(() => page.evaluate(() => window.overlayHarness.searchCalls)).toEqual(['long']);
  await page.evaluate(() => window.overlayHarness.reply('long', Array.from({length:8}, (_,i) => ({ticker:`T${i}`,name:`名称 ${i}`,sector:'行业'}))));
  await expect(palette(page).getByRole('option')).toHaveCount(8);
  for (let i=0; i<7; i++) await page.keyboard.press('ArrowDown');
  await input.dispatchEvent('keydown', {key:'Enter',isComposing:true});
  await input.dispatchEvent('keydown', {key:'Escape',isComposing:true});
  await expect(palette(page)).toBeVisible();
  await input.fill('short');
  await expect.poll(() => page.evaluate(() => window.overlayHarness.searchCalls)).toEqual(['long','short']);
  await page.evaluate(() => window.overlayHarness.reply('short', [{ticker:'ONE',name:'One',sector:'one'},{ticker:'TWO',name:'Two',sector:'two'}]));
  await page.keyboard.press('ArrowUp');
  await expect(input).toHaveAttribute('aria-activedescendant','command-palette-option-1');
  await page.keyboard.press('ArrowDown');
  await expect(input).toHaveAttribute('aria-activedescendant','command-palette-option-0');
});

test('mobile sheet can sit beneath the palette and restores scrolling after both close', async ({ page }) => {
  await page.setViewportSize({width:390,height:844}); await harness(page);
  await page.getByRole('button', {name:'更多',exact:true}).click();
  const sheet = page.getByRole('dialog', {name:'更多功能'});
  await expect(sheet).toBeVisible();
  await page.evaluate(() => window.overlayHarness.palette(true));
  await page.keyboard.press('Escape');
  await expect(sheet).toBeVisible();
  await expect.poll(() => overflow(page)).toEqual({value:'hidden',priority:'important'});
  await page.keyboard.press('Escape');
  await expect(sheet).toBeHidden();
  await expect.poll(() => overflow(page)).toEqual({value:'',priority:''});
});

test('nonmodal scan history still closes by clicking outside', async ({ page }) => {
  await harness(page); await page.getByRole('button',{name:'扫描历史'}).click();
  await expect(page.getByRole('dialog',{name:'最近扫描记录'})).toBeVisible();
  await page.locator('#background-button').click();
  await expect(page.getByRole('dialog',{name:'最近扫描记录'})).toBeHidden();
});

for (const width of [390,1440]) {
  test(`Drawer naming, close target and long palette text fit ${width}px`, async ({ page }) => {
    await page.setViewportSize({width,height:900}); await harness(page); await openDrawer(page);
    const close = await drawer(page).getByRole('button',{name:'关闭抽屉'}).boundingBox();
    expect(close.width).toBeGreaterThanOrEqual(44); expect(close.height).toBeGreaterThanOrEqual(44);
    await page.locator('#open-command').click();
    const input = palette(page).getByRole('combobox');
    await input.fill('longname');
    await expect.poll(() => page.evaluate(() => window.overlayHarness.searchCalls)).toEqual(['longname']);
    await page.evaluate(() => window.overlayHarness.reply('longname',[{ticker:'LONG',name:'这是一条用于检查窄屏布局的很长的公司名称'.repeat(8),sector:'很长的行业名称'.repeat(8)}]));
    await expect(palette(page).getByRole('option')).toHaveCount(1); await settled(page);
    await expect.poll(() => palette(page).evaluate((el) => el.scrollWidth-el.clientWidth)).toBeLessThanOrEqual(1);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth-innerWidth)).toBeLessThanOrEqual(1);
    await expect(input).toHaveAttribute('aria-expanded','true');
    await expect(input).toHaveAttribute('aria-autocomplete','list');
  });
}

test('global shortcut respects repeat, input composition and existing prevented events', async ({ page }) => {
  await page.goto('/');
  await page.keyboard.press('Control+k'); await expect(palette(page)).toBeVisible();
  await page.evaluate(() => window.dispatchEvent(new KeyboardEvent('keydown',{key:'k',ctrlKey:true,repeat:true,bubbles:true,cancelable:true})));
  await expect(palette(page)).toBeVisible();
  await page.keyboard.press('Escape'); await expect(palette(page)).toBeHidden();
  for (const extra of [{isComposing:true},{shiftKey:true},{altKey:true},{keyCode:229}]) {
    await page.evaluate((extra) => window.dispatchEvent(new KeyboardEvent('keydown',{key:'k',ctrlKey:true,bubbles:true,cancelable:true,...extra})),extra);
    await expect(palette(page)).toBeHidden();
  }
  await page.evaluate(() => { const e=new KeyboardEvent('keydown',{key:'k',ctrlKey:true,bubbles:true,cancelable:true});e.preventDefault();window.dispatchEvent(e); });
  await expect(palette(page)).toBeHidden();
  await page.keyboard.press('Control+k'); await expect(palette(page)).toBeVisible();
});


test('event details remain locked below the command palette and return focus to Drawer', async ({ page }) => {
  await harness(page); await openDrawer(page);
  await page.locator('#open-event').click();
  const details = page.getByRole('dialog', {name:/突破事件详情/});
  await expect(details).toBeVisible();
  await page.evaluate(() => window.overlayHarness.palette(true));
  await expect(palette(page).getByRole('combobox')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(details).toBeVisible();
  await expect.poll(() => overflow(page)).toEqual({value:'hidden',priority:'important'});
  await page.keyboard.press('Escape');
  await expect(details).toBeHidden();
  await expect(drawer(page)).toBeVisible();
  await expect(page.locator('#open-event')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect.poll(() => overflow(page)).toEqual({value:'',priority:''});
});

test('forced colors preserves a visible keyboard outline', async ({ page }) => {
  await page.emulateMedia({forcedColors:'active'}); await harness(page); await openDrawer(page);
  await page.keyboard.press('Tab');
  const focus = await page.evaluate(() => { const s=getComputedStyle(document.activeElement); return {outline:s.outlineStyle,width:s.outlineWidth,shadow:s.boxShadow}; });
  expect(focus).toEqual({outline:'solid',width:'2px',shadow:'none'});
});

test('a new modal can take focus while a lower Select portal is open', async ({ page }) => {
  await harness(page); await openDrawer(page);
  await drawer(page).getByRole('combobox',{name:'抽屉选择'}).click();
  await expect(page.getByRole('listbox')).toBeVisible();
  await page.evaluate(() => window.overlayHarness.palette(true));
  await expect(palette(page).getByRole('combobox')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(drawer(page)).toBeVisible();
  await expect.poll(() => overflow(page)).toEqual({value:'hidden',priority:'important'});
});

test('scroll lock restores separate overflow axes with their original priorities', async ({ page }) => {
  await harness(page);
  const original = await page.evaluate(() => {
    const style = document.body.style;
    style.setProperty('overflow-x','hidden','important');
    style.setProperty('overflow-y','scroll');
    return style.cssText;
  });
  await openDrawer(page); await page.locator('#open-command').click();
  await page.evaluate(() => window.overlayHarness.drawer(false));
  await expect.poll(() => overflow(page)).toEqual({value:'hidden',priority:'important'});
  await page.keyboard.press('Escape');
  await expect.poll(() => page.evaluate(() => document.body.style.cssText)).toBe(original);
});
