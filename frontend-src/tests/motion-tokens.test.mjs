/**
 * transitions.dev motion contract: shipped stylesheet + chrome hooks.
 * Drives src/lib/transitions.ts (not a reimplementation) and reads the
 * CSS/TSX that actually ship in the app.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
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
  ['components/shared/MenuSelect.tsx', /t-dropdown/, 't-dropdown (menu select)'],
  ['components/screener/FilterWorkbench.tsx', /<MenuSelect/, 'MenuSelect (screener)'],
  ['components/detail/OptionsPanel.tsx', /<MenuSelect/, 'MenuSelect (expirations)'],
  ['pages/Watchlist.tsx', /<MenuSelect/, 'MenuSelect (watchlist sort)'],
  ['components/catalysts/FilterBar.tsx', /<MenuSelect/, 'MenuSelect (catalyst status)'],
  ['components/shared/Segmented.tsx', /t-tabs/, 't-tabs'],
  ['components/shared/Segmented.tsx', /GlidePill/, 'GlidePill (segmented)'],
  ['components/shared/Segmented.tsx', /SPRING_INDICATOR/, 'beui spring (segmented)'],
  ['components/shared/Segmented.tsx', /layoutRoot/, 'layoutRoot (segmented)'],
  ['components/shared/Skeleton.tsx', /t-skel/, 't-skel'],
  ['components/shared/Skeleton.tsx', /t-skel-skeleton is-pulsing/, 't-skel-skeleton'],
  ['components/shared/InfoHint.tsx', /t-tt-wrap/, 't-tt-wrap'],
  ['components/shared/InfoHint.tsx', /t-tt-trigger/, 't-tt-trigger'],
  ['components/shared/InfoHint.tsx', /['"]t-tt /, 't-tt'],
  ['components/screener/SideCards.tsx', /t-acc/, 't-acc'],
  ['components/screener/SideCards.tsx', /t-acc-panel-inner/, 't-acc-panel-inner'],
  ['components/screener/FilterWorkbench.tsx', /GlidePill/, 'TierSegmented glide pill'],
  ['components/screener/FilterWorkbench.tsx', /SPRING_INDICATOR/, 'TierSegmented beui spring'],
  ['components/screener/FilterWorkbench.tsx', /layoutScroll/, 'TierSegmented scroll-aware projection'],
  ['components/shared/GlidePill.tsx', /layoutId=\{layoutId\}/, 'beui layoutId pill'],
  ['components/CommandPalette.tsx', /placeListGlide/, 'palette glide highlight'],
  ['pages/Login.tsx', /t-input-wrap/, 't-input-wrap'],
  ['pages/Login.tsx', /userShake\.classes\.wrap/, 'username shake wrap className'],
  ['pages/Login.tsx', /pwShake\.classes\.input/, 'password shake input className'],
  ['pages/Login.tsx', /t-error-track/, 'inline error height track'],
  ['components/Toast.tsx', /t-toast-row/, 't-toast-row stack collapse'],
  ['components/shared/Skeleton.tsx', /SkeletonReveal/, 'SkeletonReveal export'],
  ['pages/Watchlist.tsx', /<SkeletonReveal/, 'SkeletonReveal (watchlist)'],
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
  const workbench = await source('components/screener/FilterWorkbench.tsx');
  assert.match(workbench, /<MenuSelect/);
  const menu = await source('components/shared/MenuSelect.tsx');
  assert.match(menu, /aria-haspopup="listbox"/);
  assert.doesNotMatch(menu, /<select[\s>]/);
});

test('src ships no native select menus', async () => {
  const files = [];
  const walk = async (dir) => {
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      const p = path.join(dir, entry.name);
      if (entry.isDirectory()) await walk(p);
      else if (/\.(tsx|ts)$/.test(entry.name)) files.push(p);
    }
  };
  await walk(src);
  const hits = [];
  for (const file of files) {
    const code = await readFile(file, 'utf8');
    if (/<select[\s>]/.test(code)) hits.push(path.relative(src, file));
  }
  assert.deepEqual(hits, [], `native <select> still in ${hits.join(', ')}`);
});

test('Login renders is-error and is-shaking via React className, not classList', async () => {
  const login = await source('pages/Login.tsx');
  assert.match(login, /useCatalogShake/);
  /* 每个字段一个实例：用户名为空抖用户名框，密码问题抖密码框 */
  assert.match(login, /className=\{cn\('t-input-wrap mb-4',\s*userShake\.classes\.wrap\)\}/);
  assert.match(login, /className=\{cn\('t-input-wrap',\s*pwShake\.classes\.wrap\)\}/);
  assert.match(login, /userShake\.play\(\{ message: true \}\)/);
  assert.match(login, /pwShake\.play\(\{ message: true \}\)/);
  /* 服务端失败：抖动+红边但不带行内文案（底部 aria-live 状态行独占那份文案） */
  assert.match(login, /pwShake\.play\(\);/);
  assert.match(
    login,
    /className=\{cn\([\s\S]*pwShake\.classes\.input[\s\S]*pwShake\.error \? 'border-down-600'/,
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
  /* withMessage=false：input 照常 is-error，wrap 不亮行内文案 */
  const silent = catalogShakeStateClasses(true, true, false);
  assert.equal(silent.wrap, '');
  assert.match(silent.input, /\bis-error\b/);
});

test('loading skeletons pulse until data arrives, single motion language', async () => {
  const catalog = await source('styles/transitions-catalog.css');
  /* --pulse-count:1 是 demo 节拍；加载态必须循环，否则一秒后骨架冻住 */
  assert.match(
    catalog,
    /\.t-skel\[data-state="loading"\] \.t-skel-skeleton\.is-pulsing > \* \{\s*animation-iteration-count: infinite;/,
  );
  /* catalog pulse 接管 t-skel 后，旧 shimmer 扫光在 t-skel 内退役（不许两套叠放） */
  assert.match(catalog, /\.t-skel \.skeleton-shimmer::after \{\s*content: none;/);
  /* reveal 半边：内容层在 reveal 进文档流、done 后清掉 filter/transition */
  assert.match(catalog, /\.t-skel\[data-state="reveal"\] > \.t-skel-content/);
  assert.match(catalog, /\.t-skel\[data-state="done"\] > \.t-skel-content \{[^}]*filter: none;/);
});

test('toast rows collapse on the toast clocks so the stack never jumps', async () => {
  const catalog = await source('styles/transitions-catalog.css');
  assert.match(catalog, /\.t-toast-row \{[^}]*grid-template-rows: 0fr;[^}]*var\(--toast-close\)/s);
  assert.match(catalog, /\.t-toast-row\.is-open \{[^}]*grid-template-rows: 1fr;[^}]*var\(--toast-open\)/s);
  assert.match(catalog, /\.t-toast-row \{ transition: none !important; \}/);
  const toast = await source('components/Toast.tsx');
  assert.match(toast, /t-toast-row-inner/);
  assert.match(toast, /is-settled/);
  /* 行距在 inner 的 padding 里，容器不再用 gap（gap 不会随行塌掉） */
  assert.doesNotMatch(toast, /flex-col gap-2/);
});

test('tabs ride the beui spring indicator with Paper Terminal geometry, focus ring restored', async () => {
  /* 几何仍是 catalog 的纸面分段控件（8px 条 / 26px 高），指示器是
     beui.dev components/motion/tabs 的 layoutId 弹簧（SPRING_INDICATOR，
     170/24/1.2）；reduced-motion 由 MotionConfig 归零。
     审查 #113 修正确认：layout="position" 只动位置、宽度瞬跳，禁用；
     可横向滚动的 TierSegmented 用 layoutScroll（layoutRoot 只给 fixed 容器，
     如 MobileDock 里的 Segmented）；指示器与按钮同级，方向键从最近的
     tablist 查全体标签（wrapper 下 parentElement 只剩当前 wrapper）。 */
  const catalog = await source('styles/transitions-catalog.css');
  const adaptation = catalog.slice(catalog.indexOf('Paper Terminal color remaps'));
  assert.match(adaptation, /\.t-tabs \{[^}]*border-radius: 8px;/s);
  const motion = await source('lib/motion.ts');
  assert.match(motion, /stiffness: 170/);
  assert.match(motion, /damping: 24/);
  assert.match(motion, /mass: 1\.2/);
  const pill = await source('components/shared/GlidePill.tsx');
  assert.doesNotMatch(pill, /layout="position"/, 'position-only 投影让宽度瞬跳');
  assert.match(pill, /shadow-btn/);
  const segmented = await source('components/shared/Segmented.tsx');
  assert.match(segmented, /focus-visible:ring-2/);
  assert.match(segmented, /useReducedMotion/);
  assert.match(segmented, /layoutRoot/, 'MobileDock 是 fixed 容器，Segmented 保持 layoutRoot');
  assert.match(segmented, /closest<HTMLElement>\('\[role="tablist"\]'\)/);
  const workbench = await source('components/screener/FilterWorkbench.tsx');
  assert.match(workbench, /useReducedMotion/);
  assert.match(workbench, /layoutScroll/);
  assert.doesNotMatch(workbench, /layoutRoot/, '横向滚动 tabs 用 layoutScroll，不是 layoutRoot');
  assert.match(workbench, /closest<HTMLElement>\('\[role="tablist"\]'\)/);
  assert.doesNotMatch(workbench, /useTabsPill/, 'TierSegmented rides the beui spring pill');
});

test('MenuSelect keeps the native-select keyboard contract it replaced', async () => {
  const menu = await source('components/shared/MenuSelect.tsx');
  assert.match(menu, /ArrowDown/);
  assert.match(menu, /ArrowUp/);
  assert.match(menu, /'Home'/);
  assert.match(menu, /'End'/);
  /* Esc / 选中后焦点回触发器；Tab 移出即收起 */
  assert.match(menu, /triggerRef\.current\?\.focus\(\)/);
  assert.match(menu, /relatedTarget/);
  /* 展开后把焦点放到当前选中项 */
  assert.match(menu, /\[role="option"\]\[aria-selected="true"\]/);
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
