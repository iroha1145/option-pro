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
  placeGlide,
  readCssVar,
  replayShake,
  shakeDurationMs,
} from '../src/lib/transitions.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');
const source = (p) => readFile(path.join(src, p), 'utf8');

/** Strip comments before matching: these guards must read the code, not the
    prose next to it — a comment repeating `layoutRoot` once satisfied the
    layoutRoot guard, so deleting the prop left the suite green (审查 #113)。 */
function codeOf(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => {
      const trimmed = line.trimStart();
      return !trimmed.startsWith('//') && !trimmed.startsWith('*');
    })
    .join('\n');
}
const code = async (p) => codeOf(await source(p));

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
  ['components/shared/Segmented.tsx', /\{active && <GlidePill layoutId=\{layoutId\} \/>\}/, 'GlidePill (segmented)'],
  ['components/shared/Segmented.tsx', /layoutRoot=\{!scrollable\}/, 'layoutRoot 投影（内联 / fixed 容器）'],
  ['components/shared/Segmented.tsx', /layoutScroll=\{scrollable\}/, 'layoutScroll 投影（可横向滚动的条）'],
  ['components/shared/Skeleton.tsx', /t-skel/, 't-skel'],
  ['components/shared/Skeleton.tsx', /t-skel-skeleton is-pulsing/, 't-skel-skeleton'],
  ['components/shared/InfoHint.tsx', /t-tt-wrap/, 't-tt-wrap'],
  ['components/shared/InfoHint.tsx', /t-tt-trigger/, 't-tt-trigger'],
  ['components/shared/InfoHint.tsx', /['"]t-tt /, 't-tt'],
  ['components/screener/SideCards.tsx', /t-acc/, 't-acc'],
  ['components/screener/SideCards.tsx', /t-acc-panel-inner/, 't-acc-panel-inner'],
  ['components/screener/FilterWorkbench.tsx', /<Segmented<TierFilter>/, 'TierSegmented 走共享分段控件'],
  ['components/screener/FilterWorkbench.tsx', /^\s+scrollable$/m, 'TierSegmented 声明可横向滚动'],
  ['components/shared/GlidePill.tsx', /layoutId=\{layoutId\}/, 'beui layoutId pill'],
  ['components/shared/GlidePill.tsx', /data-glide-pill/, 'GlidePill 取证句柄'],
  [
    'components/shared/GlidePill.tsx',
    /transition=\{reduce \? \{ duration: 0 \} : SPRING_INDICATOR\}/,
    '滑块自持弹簧 + reduced-motion 归零',
  ],
  ['components/CommandPalette.tsx', /placeGlide\(/, 'palette glide highlight'],
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
    assert.match(await code(file), pattern, `${label} missing in ${file}`);
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
     170/24/1.2）；弹簧与 reduced-motion 归零都在 GlidePill 里，调用点不再包
     MotionConfig。审查 #113 修正确认：layout="position" 只动位置、宽度瞬跳，
     禁用；可横向滚动的条用 layoutScroll，其余（含 MobileDock 的 fixed 容器）
     用 layoutRoot；指示器与按钮同级，方向键从最近的 tablist 查全体标签
     （wrapper 下 parentElement 只剩当前 wrapper）。
     以下一律读 codeOf() 后的代码：注释里复读一遍 layoutRoot 不算实现。 */
  const catalog = await source('styles/transitions-catalog.css');
  /* 先按注释里的分节标记切，再剥注释：剥完标记就没了，而声明块才是断言对象 */
  const adaptation = codeOf(catalog.slice(catalog.indexOf('Paper Terminal color remaps')));
  assert.match(adaptation, /\.t-tabs \{[^}]*border-radius: 8px;/s);
  /* 滑块归 framer 之后本仓再没有 .t-tabs-pill 标记，几何适配是死样式 */
  assert.doesNotMatch(adaptation, /\.t-tabs-pill/, '无人渲染的 pill 不留适配块');
  const motion = await code('lib/motion.ts');
  assert.match(motion, /stiffness: 170/);
  assert.match(motion, /damping: 24/);
  assert.match(motion, /mass: 1\.2/);
  const pill = await code('components/shared/GlidePill.tsx');
  assert.doesNotMatch(pill, /layout="position"/, 'position-only 投影让宽度瞬跳');
  assert.match(pill, /shadow-btn/);
  /* 弹簧与归零只此一份：调用点不该再各自包 MotionConfig */
  assert.match(pill, /useReducedMotion\(\)/);
  const segmented = await code('components/shared/Segmented.tsx');
  assert.match(segmented, /focus-visible:ring-2/);
  assert.doesNotMatch(segmented, /MotionConfig/, '滑块自持 transition，调用点不再包 MotionConfig');
  assert.match(segmented, /closest<HTMLElement>\('\[role="tablist"\]'\)/);
  /* 可横向滚动时的 affordance 与不收缩的项跟着 scrollable 一起走 */
  assert.match(segmented, /scrollable && 'no-scrollbar max-w-full overflow-x-auto'/);
  assert.match(segmented, /scrollable && 'shrink-0 whitespace-nowrap'/);
  const workbench = await code('components/screener/FilterWorkbench.tsx');
  /* 键盘/结构只留共享件一份：TierSegmented 曾整段抄写并已跑偏（审计 2.5.9） */
  assert.doesNotMatch(workbench, /closest<HTMLElement>/, 'roving tabindex 只应存在于 shared/Segmented');
  assert.doesNotMatch(workbench, /onKeyDown/, 'TierSegmented 不再自持键盘实现');
  assert.doesNotMatch(workbench, /MotionConfig/);
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

function fakeGlide() {
  const style = { transition: 'transform 250ms', transform: '', width: '', height: '' };
  style.setProperty = (name, value) => {
    style[name] = value;
  };
  const el = {
    style,
    reads: 0,
    get offsetWidth() {
      el.reads += 1;
      return 1;
    },
  };
  return el;
}

test('placeGlide writes transform + the axis size, suspending the transition on first paint', () => {
  /* 横向标签条走 x（translateX + width），纵向结果列表走 y（translateY + height）。
     animate=false 是首绘/换批：掐断过渡→写→回流→还原，否则会从 translate(0)/
     size:0 补间过来。 */
  const x = fakeGlide();
  placeGlide(x, { offset: 40, size: 80 }, { axis: 'x', animate: false });
  assert.equal(x.style.transform, 'translateX(40px)');
  assert.equal(x.style.width, '80px');
  assert.equal(x.style.height, '', 'x 轴不许碰高度');
  assert.equal(x.style.transition, 'transform 250ms', '掐断后必须还原调用方的过渡');
  assert.ok(x.reads >= 1, 'must force a reflow via offsetWidth');

  const painted = x.reads;
  placeGlide(x, { offset: 120, size: 64 }, { axis: 'x', animate: true });
  assert.equal(x.style.transform, 'translateX(120px)');
  assert.equal(x.style.width, '64px');
  assert.equal(x.reads, painted, 'animate 路径要留给 CSS 补间，不强制回流');

  const y = fakeGlide();
  placeGlide(y, { offset: 18, size: 36 }, { axis: 'y', animate: false });
  assert.equal(y.style.transform, 'translateY(18px)');
  assert.equal(y.style.height, '36px');
  assert.equal(y.style.width, '', 'y 轴不许碰宽度');
  assert.equal(y.style.transition, 'transform 250ms');
  assert.ok(y.reads >= 1);

  placeGlide(y, { offset: 54, size: 40 }, { axis: 'y', animate: true });
  assert.equal(y.style.transform, 'translateY(54px)');
  assert.equal(y.style.height, '40px');
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
