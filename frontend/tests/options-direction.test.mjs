import assert from 'node:assert/strict';
import test from 'node:test';

import { renderAnalysisCard } from '../static/js/components/aiAnalysis.js';

test('unknown option direction is rendered without inventing a mixed signal', () => {
  const html = renderAnalysisCard({
    confidence: 'low',
    direction: 'unknown',
    summary: '缺少成交主动方',
  });
  assert.match(html, /方向无法判断/);
  assert.doesNotMatch(html, /多空交织/);
});
