import test from 'node:test';
import assert from 'node:assert/strict';
import { getSecurityDiagnostics } from '../src/api/modules/strengthDiagnostics.ts';
import { resetMarketReadState } from '../src/api/marketRead.ts';
import {
  diagnosticDataStatus,
  diagnosticAtrReference,
  diagnosticFamily,
  diagnosticNumber,
  diagnosticPercent,
  diagnosticReason,
  pathStatus,
} from '../src/lib/eodDiagnostics.ts';

function body(version) {
  return {
    ticker: 'SPY', profile: 'balanced', horizon: 'mid', compute_version: version,
    served_session: '2026-09-18', data_status: 'scored', paths: [],
  };
}

test('a symbol lookup sends one read, forces a fresh retry, and keeps 503 visible', async () => {
  resetMarketReadState();
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), cache: options?.cache });
    if (calls.length === 3) {
      return new Response(JSON.stringify({ detail: { code: 'eod_diagnostics_unavailable', message: 'not published' } }), {
        status: 503, headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response(JSON.stringify(body(`v${calls.length}`)), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  };
  try {
    const first = await getSecurityDiagnostics(' SPY ', 'balanced', 'mid');
    const second = await getSecurityDiagnostics('SPY', 'balanced', 'mid');
    assert.equal(first.compute_version, 'v1');
    assert.equal(second.compute_version, 'v2');
    await assert.rejects(getSecurityDiagnostics('SPY', 'balanced', 'mid'), (error) => error.code === 503);
    assert.equal(calls.length, 3);
    assert.ok(calls.every((call) => call.url.endsWith('/strength/diagnostics/SPY?profile=balanced&timeframe=mid')));
    assert.ok(calls.every((call) => call.cache === 'reload'));
  } finally {
    globalThis.fetch = originalFetch;
    resetMarketReadState();
  }
});

test('a case-sensitive provider symbol keeps its spelling in the URL and response identity', async () => {
  resetMarketReadState();
  const originalFetch = globalThis.fetch;
  let requestedUrl = '';
  globalThis.fetch = async (url) => {
    requestedUrl = String(url);
    return new Response(JSON.stringify({
      ticker: 'BCpC', requested_ticker: 'BCpC', profile: 'balanced', horizon: 'mid',
      data_status: 'scored', paths: [],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  try {
    const response = await getSecurityDiagnostics(' BCpC ', 'balanced', 'mid');
    assert.match(requestedUrl, /\/strength\/diagnostics\/BCpC\?profile=balanced&timeframe=mid$/);
    assert.equal(response.ticker, 'BCpC');
    assert.equal(response.requested_ticker, 'BCpC');
  } finally {
    globalThis.fetch = originalFetch;
    resetMarketReadState();
  }
});

test('setup rejection codes have specific explanations and residual terminology is accurate', () => {
  const codes = [
    'BELOW_SMA50', 'BREAKOUT_TRACK_EXPIRED', 'BREAKOUT_UNCONFIRMED',
    'DEPTH_OUT_OF_RANGE', 'INCOMPLETE_DAILY_DATA', 'LH_LL', 'LOW_ADV',
    'LOW_CLV', 'LOW_EVENT_RVOL', 'LOW_PRICE', 'LOW_RVOL',
    'MISSING_FIRST_DAY_CLV', 'MISSING_FIRST_DAY_RVOL', 'MISSING_RESIDUAL',
    'MISSING_SESSION_BAR', 'NONPOSITIVE_MOMENTUM', 'NONPOSITIVE_RESIDUAL',
    'NOT_THROUGH_RESISTANCE', 'NO_FROZEN_BASE', 'NO_REBOUND',
    'SMA50_SLOPE', 'SUPPORT_BROKEN', 'TOO_FAR_FROM_BASE', 'TOO_FAR_FROM_MA',
    'TREND_DIRECTION', 'UNRESOLVED_UPTHRUST', 'WEAK_STRUCTURE',
  ];
  for (const code of codes) {
    assert.doesNotMatch(diagnosticReason(code), /其他条件未通过/, code);
    assert.ok(diagnosticReason(code).length > 6, code);
  }
  assert.equal(diagnosticFamily('D_residual_momentum'), '残差动量（风险调整）');
  assert.equal(diagnosticAtrReference('legacy_all_tracks_missing_industry'), '未分类证券合并参照（含股票与基金）');
});

test('a new publication does not join the earlier in-flight diagnostic read', async () => {
  resetMarketReadState();
  const originalFetch = globalThis.fetch;
  const releases = [];
  const calls = [];
  globalThis.fetch = (url) => {
    calls.push(String(url));
    return new Promise((resolve) => releases.push(resolve));
  };
  try {
    const older = getSecurityDiagnostics('SPY', 'balanced', 'mid', 'old');
    const newer = getSecurityDiagnostics('SPY', 'balanced', 'mid', 'new');
    assert.equal(calls.length, 2);
    assert.match(calls[0], /publication=old/);
    assert.match(calls[1], /publication=new/);
    releases[1](new Response(JSON.stringify(body('new')), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    releases[0](new Response(JSON.stringify(body('old')), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    assert.equal((await newer).compute_version, 'new');
    assert.equal((await older).compute_version, 'old');
  } finally {
    globalThis.fetch = originalFetch;
    resetMarketReadState();
  }
});

test('missing diagnostics stay distinct from zero and watch is not an eligible result', () => {
  assert.equal(diagnosticNumber(null), '未提供');
  assert.equal(diagnosticNumber(0), '0.0');
  assert.equal(diagnosticPercent(null), '未提供');
  assert.equal(diagnosticPercent(0), '0.0%');
  assert.equal(pathStatus('watch'), '观察，资格尚未核实');
  assert.equal(diagnosticDataStatus('data_insufficient'), '数据不足，无法生成完整路径');
  assert.match(diagnosticReason('DOLLAR_LIQUIDITY_UNVERIFIED'), /不能视为已通过流动性门/);
});
