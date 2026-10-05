import { expect, test } from '@playwright/test';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const bundle = await build({
  stdin: { resolveDir: root, contents: `
    import React, { useEffect, useState } from 'react';
    import { createRoot } from 'react-dom/client';
    import { AccessProvider, useAccess } from './src/hooks/useAccess.tsx';
    import { usePersonalWatchlist } from './src/hooks/usePersonalWatchlist.ts';
    import { accessApi } from './src/api/modules/access.ts';
    import { accountApi } from './src/api/modules/account.ts';
    const evidence = window.evidence = { writes: [], username: 'first', failIdentity: false, access: null, oldEdit: null, current: null };
    accessApi.identity = async () => {
      if (evidence.failIdentity) throw new Error('Fixture identity unavailable');
      return { role: 'visitor', accountUsername: evidence.username, aiEnabled: false, aiAvailable: false, aiReason: null };
    };
    accountApi.watchlist = async () => ({ tickers: ['AAPL'], maxTickers: 50 });
    accountApi.edit = async (add, remove) => {
      evidence.writes.push({ add, remove });
      return { tickers: add, maxTickers: 50 };
    };
    function Personal() {
      const personal = usePersonalWatchlist();
      useEffect(() => {
        evidence.current = personal;
        if (personal.enabled && !evidence.oldEdit) evidence.oldEdit = personal.edit;
      }, [personal]);
      return React.createElement('p', { id: 'personal' }, personal.enabled ? 'enabled' : 'disabled');
    }
    function Probe() {
      const access = useAccess();
      const [visible, setVisible] = useState(true);
      useEffect(() => { evidence.access = access; }, [access]);
      return React.createElement('div', {},
        React.createElement('pre', { id: 'identity' }, JSON.stringify({ username: access.username, unavailable: access.identityUnavailable, signedIn: access.isSignedIn })),
        React.createElement('button', { onClick: () => setVisible(false) }, 'Unmount'),
        visible && React.createElement(Personal));
    }
    createRoot(document.getElementById('root')).render(React.createElement(AccessProvider, {}, React.createElement(Probe)));
  ` },
  bundle: true, write: false, format: 'iife', platform: 'browser', jsx: 'automatic',
  alias: { '@': root + '/src' }, define: { 'import.meta.env': '{"VITE_API_MODE":"live"}', 'process.env.NODE_ENV': '"development"' },
});
const html = '<div id="root"></div><script>' + bundle.outputFiles[0].text.replaceAll('</script', '<\/script') + '</script>';

for (const change of ['unavailable', 'changed', 'signed-out', 'unmounted']) {
  test(`saved edit callback refuses write after identity is ${change}`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => route.request().url().endsWith('/watchlist-hook-harness') ? route.fulfill({ contentType: 'text/html', body: html }) : route.abort());
    await page.goto('/watchlist-hook-harness');
    await expect(page.locator('#personal')).toHaveText('enabled');
    await expect.poll(() => page.evaluate(() => !!window.evidence.oldEdit)).toBe(true);
    if (change === 'unmounted') {
      await page.getByRole('button', { name: 'Unmount' }).click();
      await expect(page.locator('#personal')).toHaveCount(0);
    } else {
      await page.evaluate(async (kind) => {
        if (kind === 'unavailable') window.evidence.failIdentity = true;
        else window.evidence.username = kind === 'changed' ? 'second' : null;
        await window.evidence.access.refresh().catch(() => undefined);
      }, change);
      await expect(page.locator('#identity')).toContainText(change === 'unavailable' ? '"unavailable":true' : change === 'changed' ? '"username":"second"' : '"signedIn":false');
    }
    const result = await page.evaluate(async () => {
      try { await window.evidence.oldEdit(['MSFT'], []); return { resolved: true }; }
      catch (error) { return { rejected: true, code: error.bizCode, writes: window.evidence.writes }; }
    });
    expect(result).toEqual({ rejected: true, code: 'watchlist_identity_changed', writes: [] });
    expect(errors).toEqual([]);
  });
}
