import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.goto('/visual-tests/support/shared-motion-pr197.html');
  await expect(page.locator('#inside')).toBeVisible();
});

test('closing content immediately leaves keyboard navigation', async ({ page }) => {
  await page.locator('#toggle').focus();
  await page.keyboard.press('Enter');
  await page.keyboard.press('Tab');
  await expect(page.locator('#after')).toBeFocused();
  await expect(page.locator('#inside')).toHaveCount(0);
  await page.locator('#toggle').click();
  await page.keyboard.press('Tab');
  await expect(page.locator('#inside')).toBeFocused();
});

test('only visible loading icons animate, including nested swaps', async ({ page }) => {
  const spinner = page.locator('.t-icon-swap').first().locator('.ui-spinner');
  await expect(spinner).toHaveCSS('animation-play-state', 'paused');
  await expect(spinner).not.toHaveCSS('animation-name', 'none');
  await expect(page.locator('#nested-busy .ui-spinner')).toHaveCSS('animation-play-state', 'paused');
  await expect(page.locator('#nested-check .t-icon-swap[data-state="b"] > .t-icon[data-icon="b"]')).toHaveCSS('opacity', '1');
  await page.locator('#busy-toggle').click();
  await expect(spinner).toHaveCSS('animation-play-state', 'running');
  await expect(page.locator('#nested-busy .ui-spinner')).toHaveCSS('animation-play-state', 'running');
  await page.locator('#busy-toggle').click();
  await expect(spinner).toHaveCSS('animation-play-state', 'paused');
});

test('reduced motion keeps active loading icons still', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.locator('#busy-toggle').click();
  await expect(page.locator('.t-icon-swap').first().locator('.ui-spinner')).toHaveCSS('animation-name', 'none');
  await expect(page.locator('#nested-busy .ui-spinner')).toHaveCSS('animation-name', 'none');
});
