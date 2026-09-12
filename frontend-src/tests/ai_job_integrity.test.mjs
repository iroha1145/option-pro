import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  aiJobResultSummary,
  normalizeAiJob,
} from '../src/api/aiJobNormalize.ts';
import {
  buildOptionAlertEvidence,
  parseOptionAlertResult,
} from '../src/components/detail/optionAnalysis.ts';
import { parseSignalAnalysisResult } from '../src/components/detail/signalAnalysis.ts';

const here = path.dirname(fileURLToPath(import.meta.url));

test('live AI jobs preserve structured results and do not invent progress', () => {
  const structured = {
    output_language: 'zh-CN',
    summary: '结构化摘要。',
    analysis: '结构化正文。',
  };
  const job = normalizeAiJob({
    job_id: 'job_0123456789',
    job_type: 'option_alerts',
    status: 'in_progress',
    submitted_at: '2026-07-23T00:00:00Z',
    updated_at: '2026-07-23T00:01:00Z',
    result: structured,
  });

  assert.equal(job.status, 'in_progress');
  assert.equal(job.progress, null);
  assert.deepEqual(job.result, structured);
  assert.equal(aiJobResultSummary(job.result), '结构化摘要。');

  const measured = normalizeAiJob({
    job_id: 'job_0123456789',
    job_type: 'option_alerts',
    status: 'in_progress',
    progress: 37,
  });
  assert.equal(measured.progress, 37);
});

test('option analysis submits at most ten alerts derived from the visible chain', () => {
  const chain = {
    ticker: 'NVDA',
    expiration: '2026-08-21',
    spot: 120,
    rows: Array.from({ length: 8 }, (_, index) => ({
      strike: 90 + index * 10,
      callOi: 100 + index,
      callVol: 6_000 + index * 100,
      callIv: 0.42 + index / 100,
      callBid: 2 + index / 10,
      callAsk: 2.2 + index / 10,
      putOi: 120 + index,
      putVol: 5_500 + index * 100,
      putIv: 0.45 + index / 100,
      putBid: 1.8 + index / 10,
      putAsk: 2 + index / 10,
    })),
  };
  const original = structuredClone(chain);

  const alerts = buildOptionAlertEvidence(
    chain,
    chain.expiration,
    29,
  );

  assert.equal(alerts.length, 10);
  assert.deepEqual(chain, original);
  assert.ok(
    alerts.every(
      (alert) =>
        alert.expiration === chain.expiration &&
        alert.dte === 29 &&
        alert.volume > 0 &&
        alert.reasons.length > 0 &&
        alert.direction === 'unknown' &&
        alert.direction_status === 'unavailable_without_trade_side' &&
        alert.direction_deprecated === true,
    ),
  );
  assert.ok(alerts.some((alert) => alert.premium_flow > 0));
  assert.ok(alerts.some((alert) => alert.vol_oi_ratio > 3));
});

test('option result parser requires the complete backend contract', () => {
  const result = {
    output_language: 'zh-CN',
    confidence: 'medium',
    direction: 'unknown',
    direction_status: 'unavailable_without_trade_side',
    summary: '成交集中在少数行权价，但缺少成交主动方。',
    analysis: '现有结构化数据只能说明成交和持仓分布，不能据此判断真实买卖方向。',
    key_strikes: ['120', '125'],
    risk_note: '买卖中价估算不等于实际成交价。',
  };

  assert.deepEqual(parseOptionAlertResult(result), result);
  assert.equal(parseOptionAlertResult({ ...result, analysis: '' }), null);
  assert.equal(parseOptionAlertResult('期权解读完成'), null);
});

test('production option panel contains no hard-coded completion conclusion', async () => {
  const source = await readFile(
    path.resolve(here, '..', 'src', 'components', 'detail', 'OptionsPanel.tsx'),
    'utf8',
  );
  assert.doesNotMatch(source, /期权链解读完成/);
  assert.match(source, /alerts: evidence/);
  assert.match(source, /underlyingPrice: activeChain\.spot/);
  assert.match(source, /expiration,/);
  assert.match(source, /force,/);
  assert.match(source, /aiAvailable/);
  assert.match(source, /activeChain/);
});

function validSignalResult(overrides = {}) {
  return {
    output_language: 'zh-CN',
    asset: 'AAPL',
    horizon: '数日到数周',
    dominant_regime: '区间整理',
    trend_bias_confidence: 60,
    top_risk_confidence: 40,
    bottom_opportunity_confidence: 50,
    dip_buy_quality: 55,
    breakdown_risk: 35,
    data_quality: 80,
    final_bias: 'range_consolidation',
    top_evidence: [],
    bottom_evidence: [],
    dip_buy_evidence: [],
    bearish_evidence: [],
    contradictions: [],
    options_flow_read: {
      net_direction: 'mixed',
      confidence: 50,
      bullish_flow_evidence: [],
      bearish_flow_evidence: [],
      unknown_or_neutral_flow: [],
      warnings: [],
    },
    key_levels: {
      support: [],
      resistance: [],
      vwap_levels: [],
      options_levels: [],
    },
    confirmation_signals: [],
    invalidation_signals: [],
    event_risks: [],
    data_quality_notes: [],
    summary: '区间整理，等待方向确认。',
    ...overrides,
  };
}

test('signal analysis parser keeps the structured contract and rejects gaps', () => {
  const result = validSignalResult();
  assert.equal(parseSignalAnalysisResult(result)?.final_bias, 'range_consolidation');
  assert.equal(parseSignalAnalysisResult(result)?.options_flow_read.net_direction, 'mixed');
  assert.equal(parseSignalAnalysisResult({ ...result, summary: '' }), null);
  assert.equal(parseSignalAnalysisResult({ ...result, final_bias: 'sideways' }), null);
  assert.equal(parseSignalAnalysisResult({ summary: '只有摘要' }), null);
});

test('stock AI card hydrates existing jobs and renders structured fields', async () => {
  const source = await readFile(
    path.resolve(here, '..', 'src', 'components', 'detail', 'AiAnalysisCard.tsx'),
    'utf8',
  );
  assert.match(source, /getLatestSignalAnalysisJob/);
  assert.match(source, /parseSignalAnalysisResult/);
  assert.match(source, /aiAvailable/);
  assert.match(source, /isOwner && \(/);
  assert.doesNotMatch(source, /accordion/);
});
