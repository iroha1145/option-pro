import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { visibleFeedPage } from '../src/components/catalysts/feedSnapshot.ts';
import { toQuery } from '../src/api/client.ts';

const here = path.dirname(fileURLToPath(import.meta.url));

test('default feed path uses visible page mode and matches theme-boot query order', async () => {
  const expected = 'window_hours=72&include_unanalyzed=true&include_neutral=true&page_mode=visible&limit=12';
  assert.equal(
    toQuery({
      window_hours: 72,
      include_unanalyzed: true,
      include_neutral: true,
      page_mode: 'visible',
      limit: 12,
    }),
    expected,
  );
  const api = await readFile(path.join(here, '../src/components/catalysts/api.ts'), 'utf8');
  assert.match(api, /page_mode: q\.pageMode === null \? undefined : \(q\.pageMode \?\? 'visible'\)/);
  assert.match(api, /windowHours: 24,\s*limit: 50,[\s\S]*pageMode: null/);
  assert.match(api, /analysisStatus: 'completed',\s*limit: 50,\s*pageMode: null/);
  const boot = await readFile(path.join(here, '../public/theme-boot.js'), 'utf8');
  assert.match(boot, new RegExp(expected.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
});

test('visibleFeedPage does not hop empty pages unless asked', async () => {
  const calls = [];
  const read = async (cursor) => {
    calls.push(cursor ?? '');
    return {
      items: [],
      nextCursor: cursor === 'c1' ? null : 'c1',
      total: 20,
      hiddenUnanalyzed: 12,
    };
  };
  const page = await visibleFeedPage(read);
  assert.deepEqual(calls, ['']);
  assert.equal(page.nextCursor, 'c1');
  assert.equal(page.hiddenUnanalyzed, 12);

  const hopped = await visibleFeedPage(read, undefined, { hopEmptyPages: true });
  assert.deepEqual(calls, ['', '', 'c1']);
  assert.equal(hopped.nextCursor, null);
  assert.equal(hopped.hiddenUnanalyzed, 24);
});
