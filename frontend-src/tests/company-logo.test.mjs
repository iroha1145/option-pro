import assert from 'node:assert/strict';
import test from 'node:test';
import { companyLogoSource, companySymbol } from '../src/lib/companyLogo.ts';

test('公司标识识别规范化股票代码，指数不误取公司图片', () => {
  assert.equal(companySymbol(' us.tsla '), 'TSLA');
  assert.equal(companyLogoSource(' us.tsla '), '/static/company-logos/TSLA.png');
  assert.equal(companyLogoSource('SPX'), null);
  assert.equal(companyLogoSource('^NDX'), null);
});

test('只用本地清单里的静态图，清单外的代码不再请求标志接口', () => {
  assert.equal(companyLogoSource('TSLA'), '/static/company-logos/TSLA.png');
  assert.equal(companyLogoSource('CRDO'), null);
  assert.equal(companyLogoSource('BRK.B'), null);
});

test('无效代码不会成为图片地址或路径', () => {
  for (const value of ['', '../TSLA', 'TSLA?', 'TSLA/..', 'A--B', 'A..B', 'A.', 'https://example.com']) {
    assert.equal(companyLogoSource(value), null, value);
  }
});
