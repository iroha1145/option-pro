/**
 * 新闻页栏目行右侧的「更多」菜单（2026-10-08 第二版）：键盘可达、Esc 关闭、焦点回到触发器。
 * 组件用 React 桩驱动真实源码；没有真 DOM，所以把三个 ref（根、触发器、菜单）换成记录 focus 的假节点。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';
import { createReactStub } from './helpers/react-hooks.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const srcRoot = path.resolve(here, '../src');

function compile(rel, imports, globals = {}) {
  const source = fs.readFileSync(path.join(srcRoot, rel), 'utf8');
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, {
    module,
    exports: module.exports,
    console,
    ...globals,
    require(id) {
      if (id in imports) return imports[id];
      throw new Error(`Unexpected import ${id} in ${rel}`);
    },
  });
  return module.exports;
}

const passthroughJsx = {
  jsx: (type, props) => ({ type, props }),
  jsxs: (type, props) => ({ type, props }),
  Fragment: 'Fragment',
};

function childNodes(node) {
  if (!node || typeof node !== 'object' || !node.props) return [];
  const out = [];
  for (const [key, value] of Object.entries(node.props)) {
    if (key === 'children') out.push(value);
    else if (value && typeof value === 'object' && !('current' in value) && (Array.isArray(value) || 'props' in value)) out.push(value);
  }
  return out;
}

function collectText(node, out = []) {
  if (node == null || node === false || node === true) return out;
  if (typeof node === 'string' || typeof node === 'number') {
    out.push(String(node));
    return out;
  }
  if (Array.isArray(node)) {
    for (const child of node) collectText(child, out);
    return out;
  }
  if (typeof node === 'object' && node.props) for (const child of childNodes(node)) collectText(child, out);
  return out;
}
const textOf = (node) => collectText(node).join(' ').replace(/\s+/g, ' ').trim();

function findAll(node, predicate, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (Array.isArray(node)) {
    for (const child of node) findAll(child, predicate, out);
    return out;
  }
  if (node.props && predicate(node)) out.push(node);
  for (const child of childNodes(node)) findAll(child, predicate, out);
  return out;
}
const findNode = (tree, predicate) => findAll(tree, predicate)[0] ?? null;

function harness({ current = null, showManage = false } = {}) {
  const runner = createReactStub();
  const refs = [];
  const baseUseRef = runner.React.useRef;
  runner.React.useRef = (initial) => {
    const ref = baseUseRef(initial);
    if (!refs.includes(ref)) refs.push(ref);
    return ref;
  };
  const focusLog = [];
  const selected = [];
  const listeners = new Map();
  const doc = {
    activeElement: null,
    addEventListener: (type, fn) => { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); },
    removeEventListener: (type, fn) => listeners.get(type)?.delete(fn),
  };
  const emit = (type, event) => { for (const fn of [...(listeners.get(type) ?? [])]) fn(event); };
  const MoreMenu = compile('components/catalysts/MoreMenu.tsx', {
    react: runner.React,
    'react/jsx-runtime': passthroughJsx,
    '@/lib/utils': { cn: (...xs) => xs.filter(Boolean).join(' ') },
    '@/lib/transitions': {
      overlayClassName: () => '',
      overlayVisible: (open) => open,
      readRootDurationMs: () => 0,
      useOverlayPhase: (open) => (open ? 'open' : 'closed'),
    },
    '@/components/icons': { default: 'Icon' },
    '../../i18n/core.ts': { t: (msgid) => msgid },
  }, { document: doc }).default;
  const props = { current, showManage, onSelect: (view) => selected.push(view) };
  const read = runner.mount(() => MoreMenu(props));
  // useRef 的创建顺序：根容器、触发器、菜单。
  const [rootRef, triggerRef, menuRef] = refs;
  const inside = { name: 'inside' };
  rootRef.current = { contains: (node) => node === inside };
  triggerRef.current = { focus: () => { focusLog.push('trigger'); doc.activeElement = triggerRef.current; } };
  /* 菜单项的假节点按渲染结果即时生成（同一标签复用同一个对象，document.activeElement 才能比对）。 */
  const fakeItems = new Map();
  const itemNodes = () => {
    const menu = findNode(read(), (node) => node.props.role === 'menu');
    if (!menu) return [];
    return findAll(menu, (node) => node.props.role === 'menuitem').map((item) => {
      const label = textOf(item);
      if (!fakeItems.has(label)) {
        const fake = { label, focus: () => { focusLog.push(`item:${label}`); doc.activeElement = fake; } };
        fakeItems.set(label, fake);
      }
      const fake = fakeItems.get(label);
      fake.current = item.props['aria-current'] === 'true';
      return fake;
    });
  };
  menuRef.current = {
    querySelector: (selector) => {
      const items = itemNodes();
      if (selector.includes('aria-current')) return items.find((item) => item.current) ?? null;
      return items[0] ?? null;
    },
    querySelectorAll: () => itemNodes(),
  };
  return {
    tree: () => read(),
    trigger: () => findNode(read(), (node) => node.type === 'button' && node.props['aria-haspopup'] === 'menu'),
    menu: () => findNode(read(), (node) => node.props.role === 'menu'),
    items: () => findAll(read(), (node) => node.props.role === 'menuitem'),
    focusLog,
    selected,
    inside,
    emit,
    itemNodes,
    unmount: () => runner.unmount(),
  };
}

test('触发器是菜单按钮：默认写「更多」，停在来源或管理设置时写当前项的名字并高亮', () => {
  const closed = harness();
  assert.equal(closed.trigger().props['aria-haspopup'], 'menu');
  assert.equal(closed.trigger().props['aria-expanded'], false);
  assert.equal(textOf(closed.trigger()), '更多');
  assert.equal(closed.menu(), null);
  closed.unmount();

  const sources = harness({ current: 'sources' });
  assert.equal(textOf(sources.trigger()), '消息来源');
  assert.match(sources.trigger().props.className, /border-brand-400/);
  sources.unmount();

  const manage = harness({ current: 'manage', showManage: true });
  assert.equal(textOf(manage.trigger()), '管理设置');
  manage.unmount();
});

test('菜单项：访客只有消息来源，所有者多一个管理设置', () => {
  for (const [showManage, expected] of [[false, ['消息来源']], [true, ['消息来源', '管理设置']]]) {
    const h = harness({ showManage });
    h.trigger().props.onClick();
    assert.equal(h.trigger().props['aria-expanded'], true);
    assert.equal(h.menu().props['aria-label'], '更多');
    assert.deepEqual(h.items().map(textOf), expected);
    assert.ok(h.items().every((item) => item.props.tabIndex === -1), '菜单项不进 Tab 序列，方向键移动');
    h.unmount();
  }
});

test('选中菜单项：通知页面、关闭菜单、焦点回到触发器', () => {
  const h = harness({ showManage: true });
  h.trigger().props.onClick();
  h.items()[1].props.onClick();
  assert.deepEqual(h.selected, ['manage']);
  assert.equal(h.menu(), null);
  assert.equal(h.trigger().props['aria-expanded'], false);
  assert.equal(h.focusLog.at(-1), 'trigger');
  h.unmount();
});

test('方向键、Home、End 在菜单项间移动；Tab 离开时关闭菜单且不拦截默认行为', () => {
  const h = harness({ showManage: true });
  h.trigger().props.onClick();
  const press = (key) => {
    const event = { key, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    h.menu().props.onKeyDown(event);
    return event;
  };
  h.itemNodes()[0].focus();
  assert.equal(press('ArrowDown').defaultPrevented, true);
  assert.equal(h.focusLog.at(-1), 'item:管理设置');
  press('ArrowDown');
  assert.equal(h.focusLog.at(-1), 'item:管理设置', '到底不循环');
  press('Home');
  assert.equal(h.focusLog.at(-1), 'item:消息来源');
  press('End');
  assert.equal(h.focusLog.at(-1), 'item:管理设置');
  assert.equal(press('a').defaultPrevented, false, '其他键不处理');
  const tab = press('Tab');
  assert.equal(tab.defaultPrevented, false, 'Tab 交给浏览器移动焦点');
  assert.equal(h.menu(), null, 'Tab 离开时菜单关闭');
  h.unmount();
});

test('触发器上的上下方向键打开菜单；Esc 关闭并把焦点还给触发器；点菜单外关闭', () => {
  const h = harness();
  const key = { key: 'ArrowDown', preventDefault() { this.defaultPrevented = true; } };
  h.trigger().props.onKeyDown(key);
  assert.equal(key.defaultPrevented, true);
  assert.ok(h.menu(), '方向键打开');

  h.emit('keydown', { key: 'Enter' });
  assert.ok(h.menu(), '其他键不关');
  h.emit('keydown', { key: 'Escape' });
  assert.equal(h.menu(), null, 'Esc 关闭');
  assert.equal(h.focusLog.at(-1), 'trigger', 'Esc 后焦点回到触发器');

  h.trigger().props.onClick();
  h.emit('mousedown', { target: h.inside });
  assert.ok(h.menu(), '点在组件内部不关');
  h.emit('mousedown', { target: { name: 'elsewhere' } });
  assert.equal(h.menu(), null, '点菜单外关闭');
  h.unmount();
});

test('打开后焦点进入菜单：优先当前项，否则第一项', () => {
  const onManage = harness({ current: 'manage', showManage: true });
  onManage.trigger().props.onClick();
  assert.equal(onManage.focusLog.at(-1), 'item:管理设置');
  onManage.unmount();

  const none = harness({ showManage: true });
  none.trigger().props.onClick();
  assert.equal(none.focusLog.at(-1), 'item:消息来源');
  none.unmount();
});
