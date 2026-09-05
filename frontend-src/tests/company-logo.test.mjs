import assert from 'node:assert/strict';
import test from 'node:test';
import { companyLogoSources, companySymbol } from '../src/lib/companyLogo.ts';

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
