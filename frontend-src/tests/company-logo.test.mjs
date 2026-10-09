import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { createReactStub } from './helpers/react-hooks.mjs';
import { companyLogoSource, companySymbol } from '../src/lib/companyLogo.ts';

const VISITOR = { signedIn: false, mock: false };
const SIGNED_IN = { signedIn: true, mock: false };

test('公司标识识别规范化股票代码，指数不误取公司图片', () => {
  assert.equal(companySymbol(' us.tsla '), 'TSLA');
  assert.equal(companyLogoSource(' us.tsla ', VISITOR), '/static/company-logos/TSLA.png');
  for (const index of ['SPX', '^NDX', 'DJI']) {
    assert.equal(companyLogoSource(index, VISITOR), null, index);
    assert.equal(companyLogoSource(index, SIGNED_IN), null, index);
  }
});

test('清单里的代码只用随前端发布的静态图；清单外的代码只有登录会话才走标志接口', () => {
  assert.equal(companyLogoSource('TSLA', VISITOR), '/static/company-logos/TSLA.png');
  assert.equal(companyLogoSource('TSLA', SIGNED_IN), '/static/company-logos/TSLA.png');
  assert.equal(companyLogoSource('CRDO', VISITOR), null);
  assert.equal(companyLogoSource('CRDO', SIGNED_IN), '/api/stocks/CRDO/logo');
  assert.equal(companyLogoSource('BRK.B', SIGNED_IN), '/api/stocks/BRK.B/logo');
  // 演示数据没有后端，登录了也不请求接口
  assert.equal(companyLogoSource('CRDO', { signedIn: true, mock: true }), null);
});

test('无效代码不会成为图片地址或路径', () => {
  for (const value of ['', '../TSLA', 'TSLA?', 'TSLA/..', 'A--B', 'A..B', 'A.', 'https://example.com']) {
    assert.equal(companyLogoSource(value, SIGNED_IN), null, value);
  }
});

/* ---------- 组件行为：按登录态渲染出哪些图片请求 ---------- */

const jsx = { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
const componentSource = fs.readFileSync(new URL('../src/components/shared/TickerLogo.tsx', import.meta.url), 'utf8');
const componentCode = ts.transpileModule(componentSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

function mountLogo(ticker, { signedIn, mock = false }) {
  const runner = createReactStub();
  runner.React.memo = (component) => component;
  const module = { exports: {} };
  vm.runInNewContext(componentCode, {
    module,
    exports: module.exports,
    require(id) {
      if (id === 'react/jsx-runtime') return jsx;
      if (id === 'react') return runner.React;
      if (id === '@/api/client') return { isMock: mock };
      if (id === '@/hooks/useAccess') return { useSignedIn: () => signedIn };
      if (id === '@/lib/companyLogo') return { companyLogoSource, companySymbol };
      if (id === '@/lib/utils') return { cn: (...parts) => parts.filter(Boolean).join(' ') };
      throw new Error(`Unexpected import: ${id}`);
    },
  });
  const TickerLogo = module.exports.default;
  // 外层算出地址，内层按地址渲染方框；两层在同一次渲染里展开
  return runner.mount((props) => {
    const mark = TickerLogo(props);
    return mark.type(mark.props);
  }, { ticker, size: 28 });
}

const imageOf = (box) => (box.props.children && typeof box.props.children === 'object' ? box.props.children : null);

function requestedImages(tickers, session) {
  return tickers.map((ticker) => imageOf(mountLogo(ticker, session)())?.props.src ?? null);
}

test('访客不发标志接口请求；登录会话只对清单外的代码请求接口', () => {
  const tickers = ['TSLA', 'CRDO', 'BRK.B', 'SPX'];
  assert.deepEqual(requestedImages(tickers, { signedIn: false }), ['/static/company-logos/TSLA.png', null, null, null]);
  assert.deepEqual(requestedImages(tickers, { signedIn: true }), ['/static/company-logos/TSLA.png', '/api/stocks/CRDO/logo', '/api/stocks/BRK.B/logo', null]);
  assert.deepEqual(requestedImages(tickers, { signedIn: true, mock: true }), ['/static/company-logos/TSLA.png', null, null, null]);
});

test('接口图片懒加载、方框尺寸固定，加载失败显示首字母且不重试', () => {
  const view = mountLogo('CRDO', { signedIn: true });
  const box = view();
  const image = imageOf(box);
  assert.equal(image.props.loading, 'lazy');
  assert.equal(image.props.width, 28);
  assert.equal(image.props.height, 28);
  assert.deepEqual([box.props.style.width, box.props.style.height], [28, 28]);

  image.props.onError();
  const failed = view();
  assert.equal(imageOf(failed), null, '失败后不再挂图片，也就不会再发请求');
  assert.equal(failed.props.children, 'C');
  assert.equal(failed.props['data-logo-state'], 'fallback');
  assert.deepEqual([failed.props.style.width, failed.props.style.height], [28, 28]);
});

test('访客看到的清单外代码直接是首字母方框', () => {
  const box = mountLogo('CRDO', { signedIn: false })();
  assert.equal(imageOf(box), null);
  assert.equal(box.props.children, 'C');
  assert.equal(box.props['data-logo-state'], 'fallback');
});
