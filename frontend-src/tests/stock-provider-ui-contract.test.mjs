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
  path.resolve(here, '..', 'src', 'components', 'StockDrawerBody.tsx'),
  'utf8',
);
const frontendRoot = path.resolve(here, '..', 'src');

function readTree(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    return entry.isDirectory() ? readTree(target) : [fs.readFileSync(target, 'utf8')];
  }).join('\n');
}

test('stock detail preserves and displays real quote providers', () => {
  assert.match(stocksSource, /priceProvider:\s*pickS\(r,\s*'priceProvider',\s*'price_provider'\)/);
  assert.match(stocksSource, /profileProvider:\s*pickS\(r,\s*'profileProvider',\s*'profile_provider'\)/);
  assert.match(drawerSource, /detail\.priceProvider/);
  assert.match(drawerSource, /detail\.profileProvider/);
  assert.equal(readTree(frontendRoot).includes('来源：Optix Research'), false);
});
