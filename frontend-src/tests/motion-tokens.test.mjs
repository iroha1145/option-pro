/**
 * transitions.dev motion contract: shipped stylesheet + chrome hooks.
 * Drives src/lib/transitions.ts (not a reimplementation) and reads the
 * CSS/TSX that actually ship in the app.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  catalogShakeStateClasses,
  overlayClassName,
  overlayDataOpen,
  overlayTiming,
  overlayVisible,
  parseDurationMs,
  placeTabsPill,
  readCssVar,
  replayShake,
  shakeDurationMs,
} from '../src/lib/transitions.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');
const source = (p) => readFile(path.join(src, p), 'utf8');

const TOKEN_NAMES = [
  '--duration-stagger',
  '--duration-micro',
  '--duration-quick',
  '--duration-fast',
  '--duration-medium',
  '--duration-slow',
  '--duration-very-slow',
  '--ease-smooth-out',
  '--ease-in-out',
  '--ease-out',
  '--ease-linear',
  '--ease-bounce',
  '--ease-bounce-strong',
  '--distance-micro',
  '--distance-small',
  '--distance-base',
  '--distance-medium',
  '--distance-large',
  '--scale-large',
  '--scale-medium',
  '--scale-small',
  '--scale-tiny',
  '--blur-small',
  '--blur-medium',
  '--blur-large',
];

const SURFACES = [
  ['components/Toast.tsx', /className=\{cn\(\s*'t-toast/, 't-toast'],
  ['components/Drawer.tsx', /t-panel-slide/, 't-panel-slide'],
  ['components/CommandPalette.tsx', /['"]t-modal/, 't-modal (command palette)'],
  ['components/catalysts/ConfirmDialog.tsx', /['"]t-modal/, 't-modal (confirm)'],
  ['components/LanguageSwitcher.tsx', /t-dropdown/, 't-dropdown'],
  ['components/shared/Segmented.tsx', /t-tabs/, 't-tabs'],
  ['components/shared/Segmented.tsx', /t-tabs-pill/, 't-tabs-pill'],
  ['components/shared/Segmented.tsx', /placeTabsPill/, 'placeTabsPill'],
  ['components/shared/Skeleton.tsx', /t-skel/, 't-skel'],
  ['components/shared/Skeleton.tsx', /t-skel-skeleton is-pulsing/, 't-skel-skeleton'],
  ['components/shared/InfoHint.tsx', /t-tt-wrap/, 't-tt-wrap'],
  ['components/shared/InfoHint.tsx', /t-tt-trigger/, 't-tt-trigger'],
  ['components/shared/InfoHint.tsx', /['"]t-tt /, 't-tt'],
  ['components/screener/SideCards.tsx', /t-acc/, 't-acc'],
  ['components/screener/SideCards.tsx', /t-acc-panel-inner/, 't-acc-panel-inner'],
  ['pages/Login.tsx', /t-input-wrap/, 't-input-wrap'],
  ['pages/Login.tsx', /shake\.classes\.wrap/, 'shake wrap className'],
  ['pages/Login.tsx', /shake\.classes\.input/, 'shake input className'],
];

test('motion-token custom properties ship in transitions-root.css', async () => {
  const css = await source('styles/transitions-root.css');
  for (const name of TOKEN_NAMES) {
    assert.ok(readCssVar(css, name), `missing token ${name}`);
  }
  assert.equal(parseDurationMs(readCssVar(css, '--duration-fast') ?? '', 0), 250);
  assert.equal(parseDurationMs(readCssVar(css, '--duration-quick') ?? '', 0), 150);
  assert.equal(parseDurationMs(readCssVar(css, '--duration-slow') ?? '', 0), 400);
  assert.equal(parseDurationMs(readCssVar(css, '--duration-medium') ?? '', 0), 350);
});

test('main.tsx imports the root token sheet once, before Paper Terminal CSS', async () => {
  const main = await source('main.tsx');
  assert.match(main, /import '\.\/styles\/transitions-root\.css'/);
  assert.match(main, /import '\.\/index\.css'/);
  assert.match(main, /import '\.\/styles\/transitions-catalog\.css'/);
  const rootAt = main.indexOf('transitions-root.css');
  const paperAt = main.indexOf('./index.css');
  const catalogAt = main.indexOf('transitions-catalog.css');
  assert.ok(rootAt < paperAt && paperAt < catalogAt, 'token sheet → paper → catalog');
});

test('index.css does not duplicate the transitions-root :root block', async () => {
  const index = await source('index.css');
  assert.doesNotMatch(index, /--duration-stagger:/);
  assert.doesNotMatch(index, /--dropdown-open-dur:/);
});

test('each catalog snippet keeps prefers-reduced-motion', async () => {
  const catalog = await source('styles/transitions-catalog.css');
  const snippets = [
    '.t-toast',
    '.t-panel-slide',
    '.t-modal',
    '.t-dropdown',
    '.t-tabs-pill',
    '.t-skel-skeleton',
    '.t-tt',
    '.t-acc-panel',
    '.t-input',
  ];
  for (const sel of snippets) {
    const idx = catalog.indexOf(sel);
    assert.ok(idx >= 0, `catalog missing ${sel}`);
  }
  const guards = catalog.match(/@media \(prefers-reduced-motion: reduce\)/g) ?? [];
  assert.ok(guards.length >= 9, `expected ≥9 reduced-motion guards, got ${guards.length}`);
  assert.match(catalog, /\.t-toast \{ transition: none !important; \}/);
  assert.match(catalog, /\.t-modal \{ transition: none !important; \}/);
  assert.match(catalog, /\.t-dropdown \{ transition: none !important; \}/);
  assert.match(catalog, /\.t-panel-slide \{ transition: none !important; \}/);
});

test('overlay close timing is shorter than open (dropdown/modal/panel)', async () => {
  const root = await source('styles/transitions-root.css');
  const modal = overlayTiming(root, '--modal-open-dur', '--modal-close-dur', 250, 150);
  const drop = overlayTiming(root, '--dropdown-open-dur', '--dropdown-close-dur', 250, 150);
  const panel = overlayTiming(root, '--panel-open-dur', '--panel-close-dur', 400, 350);
  assert.equal(modal.open, 250);
  assert.equal(modal.close, 150);
  assert.ok(modal.close < modal.open, 'modal close must be quicker than open');
  assert.equal(drop.open, 250);
  assert.equal(drop.close, 150);
  assert.ok(drop.close < drop.open);
  assert.equal(panel.open, 400);
  assert.equal(panel.close, 350);
  assert.ok(panel.close < panel.open, 'panel open slower than close');
  const toast = overlayTiming(root, '--toast-open', '--toast-close', 350, 250);
  assert.ok(toast.close < toast.open);
});

test('chrome sources wire documented t-* hooks and drop stacked framer enter/exit', async () => {
  for (const [file, pattern, label] of SURFACES) {
    const code = await source(file);
    assert.match(code, pattern, `${label} missing in ${file}`);
  }
  const toast = await source('components/Toast.tsx');
  assert.doesNotMatch(toast, /from 'framer-motion'/);
  assert.doesNotMatch(toast, /AnimatePresence/);
  const drawer = await source('components/Drawer.tsx');
  assert.doesNotMatch(drawer, /from 'framer-motion'/);
  const palette = await source('components/CommandPalette.tsx');
  assert.doesNotMatch(palette, /from 'framer-motion'/);
  const dialog = await source('components/catalysts/ConfirmDialog.tsx');
  assert.doesNotMatch(dialog, /from 'framer-motion'/);
  const lang = await source('components/LanguageSwitcher.tsx');
  assert.doesNotMatch(lang, /from 'framer-motion'/);
  const method = await source('components/screener/SideCards.tsx');
  assert.doesNotMatch(method, /AnimatePresence/);
  const login = await source('pages/Login.tsx');
  assert.doesNotMatch(login, /animate-nudge-shake/);
});

test('Login renders is-error and is-shaking via React className, not classList', async () => {
  const login = await source('pages/Login.tsx');
  assert.match(login, /useCatalogShake/);
  assert.match(login, /className=\{cn\(\s*'t-input-wrap',\s*shake\.classes\.wrap\)\}/);
  assert.match(
    login,
    /className=\{cn\([\s\S]*shake\.classes\.input[\s\S]*shake\.error \? 'border-down-600'/,
  );
  assert.doesNotMatch(login, /classList\.add\(/);
  assert.doesNotMatch(login, /classList\.remove\(/);
  assert.doesNotMatch(login, /replayShake\(/);

  const on = catalogShakeStateClasses(true, true);
  assert.match(on.wrap, /\bis-error\b/);
  assert.match(on.input, /\bis-error\b/);
  assert.match(on.input, /\bis-shaking\b/);
  const off = catalogShakeStateClasses(false, false);
  assert.equal(off.wrap, '');
  assert.equal(off.input, '');
  assert.doesNotMatch(catalogShakeStateClasses(true, false).input, /\bis-shaking\b/);
});

test('overlayClassName / overlayVisible drive the documented state classes', () => {
  assert.equal(overlayClassName('open'), 'is-open');
  assert.equal(overlayClassName('closing'), 'is-closing');
  assert.equal(overlayClassName('preopen'), '');
  assert.equal(overlayClassName('closed'), '');
  assert.equal(overlayDataOpen('open'), 'true');
  assert.equal(overlayDataOpen('closing'), 'false');
  assert.equal(overlayVisible(true, 'closed'), true);
  assert.equal(overlayVisible(false, 'closing'), true);
  assert.equal(overlayVisible(false, 'closed'), false);
});

test('placeTabsPill first paint writes transform/width without a transition', () => {
  let reads = 0;
  const pill = {
    style: { transition: 'transform 250ms', transform: '', width: '' },
    get offsetWidth() {
      reads += 1;
      return 1;
    },
  };
  placeTabsPill(pill, { offsetLeft: 40, offsetWidth: 80 }, false);
  assert.equal(pill.style.transform, 'translateX(40px)');
  assert.equal(pill.style.width, '80px');
  assert.equal(pill.style.transition, 'transform 250ms');
  assert.ok(reads >= 1, 'must force a reflow via offsetWidth');

  placeTabsPill(pill, { offsetLeft: 120, offsetWidth: 64 }, true);
  assert.equal(pill.style.transform, 'translateX(120px)');
  assert.equal(pill.style.width, '64px');
});

test('replayShake removes, reflows, then re-adds is-shaking', async () => {
  const root = await source('styles/transitions-root.css');
  assert.equal(shakeDurationMs(root), 280);

  const classes = new Set();
  let reads = 0;
  const el = {
    classList: {
      remove: (name) => classes.delete(name),
      add: (name) => classes.add(name),
    },
    get offsetWidth() {
      reads += 1;
      assert.equal(classes.has('is-shaking'), false, 'class must be off during reflow');
      return 1;
    },
  };
  classes.add('is-shaking');
  replayShake(el);
  assert.equal(classes.has('is-shaking'), true);
  assert.ok(reads >= 1);
});
