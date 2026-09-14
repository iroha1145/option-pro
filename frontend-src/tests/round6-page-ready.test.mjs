import assert from 'node:assert/strict';
import test from 'node:test';
import { classifyPageReady } from '../../scripts/perf/lib/page_ready.mjs';

test('laboratory ready classifier recognizes the catalysts page', () => {
  assert.equal(
    classifyPageReady({
      path: '/catalysts',
      heading: '新闻催化剂',
      bodyText: '',
      hasNewsArticle: true,
    }),
    'content',
  );
  assert.equal(
    classifyPageReady({
      path: '/catalysts',
      heading: 'Catalysts',
      bodyText: 'No news in this window',
      hasNewsArticle: false,
    }),
    'empty',
  );
  assert.equal(
    classifyPageReady({
      path: '/catalysts',
      heading: '',
      bodyText: '',
      hasNewsArticle: false,
    }),
    'pending',
  );
});
