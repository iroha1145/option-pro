import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { companyLogoSources, companySymbol } from '../src/lib/companyLogo.ts';

const srcDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src');

test('公司标识识别规范化股票代码，指数不误取公司图片', () => {
  assert.equal(companySymbol(' us.tsla '), 'TSLA');
  assert.deepEqual(companyLogoSources(' us.tsla ', true), ['/static/company-logos/TSLA.png']);
  assert.deepEqual(companyLogoSources('SPX', false), []);
  assert.deepEqual(companyLogoSources('^NDX', true), []);
});

test('演示仅用本地图标，真实模式支持未预存公司的同源标识接口', () => {
  assert.deepEqual(companyLogoSources('CRDO', true), []);
  assert.deepEqual(companyLogoSources('CRDO', false), ['/api/stocks/CRDO/logo']);
  assert.deepEqual(companyLogoSources('BRK.B', false), ['/api/stocks/BRK.B/logo']);
  assert.deepEqual(companyLogoSources('TSLA', false), ['/static/company-logos/TSLA.png', '/api/stocks/TSLA/logo']);
});

test('无效代码不会成为图片地址或路径', () => {
  for (const value of ['', '../TSLA', 'TSLA?', 'TSLA/..', 'A--B', 'A..B', 'A.', 'https://example.com']) {
    assert.deepEqual(companyLogoSources(value, false), [], value);
  }
});

test('加载中先显示字母，首页首屏标识不懒加载', () => {
  const logo = readFileSync(path.join(srcDir, 'components/shared/TickerLogo.tsx'), 'utf8');
  const home = readFileSync(path.join(srcDir, 'pages/Home.tsx'), 'utf8');
  assert.match(logo, /const fallback = ticker\.replace/);
  assert.match(logo, /loaded && source && 'invisible'/);
  assert.match(logo, /loading=\{priority \? 'eager' : 'lazy'\}/);
  assert.equal([...home.matchAll(/<TickerLogo /g)].length, 3);
  assert.equal([...home.matchAll(/<TickerLogo [^/>]*\bpriority/g)].length, 3);
});
