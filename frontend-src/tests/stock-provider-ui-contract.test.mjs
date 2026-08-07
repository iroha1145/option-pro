import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const stocksSource = fs.readFileSync(
  path.resolve(here, '..', 'src', 'api', 'modules', 'stocks.ts'),
  'utf8',
);
const drawerSource = fs.readFileSync(
  path.resolve(here, '..', 'src', 'pages', 'StockDetail.tsx'),
  'utf8',
);
const frontendRoot = path.resolve(here, '..', 'src');

function readTree(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    return entry.isDirectory() ? readTree(target) : [fs.readFileSync(target, 'utf8')];
  }).join('\n');
}

test('stock detail keeps real provider fields in the data layer without printing them to readers', () => {
  assert.match(stocksSource, /priceProvider:\s*pickS\(r,\s*'priceProvider',\s*'price_provider'\)/);
  assert.match(stocksSource, /profileProvider:\s*pickS\(r,\s*'profileProvider',\s*'profile_provider'\)/);
  /* 面向普通读者的详情页不印供应商名与库名，只保留「延迟行情」这一条口径 */
  assert.doesNotMatch(drawerSource, /detail\.priceProvider|detail\.profileProvider/);
  assert.match(drawerSource, /行情为延迟数据/);
  assert.equal(readTree(frontendRoot).includes('来源：Optix Research'), false);
});
