/**
 * 「缺失不等于 0」回归（GPT-5.6-Pro 审计 P1-02 / P1-03 / P1-13 / P2-4 / P2-8 / P2-9 / P2-14 / P2-15）
 *
 * 这一批缺陷的共同形状是：上游没有给的数值被补成 0，或者请求失败被画成一个真实业务
 * 状态。测试因此按「事实」而不是按渲染文案组织：适配层输出的必须是 null，判定层必须
 * 把持仓量为 0 与持仓量不可用分开。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';

import {
  buildOptionAlertEvidence,
  midpoint,
  volOiState,
} from '../src/components/detail/optionAnalysis.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');

async function source(relativePath) {
  return readFile(path.join(src, relativePath), 'utf8');
}

/**
 * 去掉注释后的源码。
 * 「不得再出现 X」这类断言必须只看可执行代码：解释「旧写法 X 为什么错」的注释
 * 里正好也含有 X，否则修复说明本身会把断言弄假。
 */
function codeOf(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => {
      const trimmed = line.trimStart();
      return !trimmed.startsWith('//') && !trimmed.startsWith('*');
    })
    .join('\n');
}

/** 与 options-provider-fallback-contract 相同的加载方式，只是这里要检查映射结果。 */
function loadModule(relativePath, extraRequires = {}) {
  const modulePath = path.join(src, relativePath);
  const compiled = ts.transpileModule(fs.readFileSync(modulePath, 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
  }).outputText;
  const asRec = (value) =>
    value !== null && typeof value === 'object' && !Array.isArray(value)
      ? value
      : {};
  const live = {
    asRec,
    pickN: (row, ...keys) => {
      for (const key of keys) {
        const value = asRec(row)[key];
        if (typeof value === 'number' && Number.isFinite(value)) return value;
      }
      return null;
    },
    pickS: (row, ...keys) => {
      for (const key of keys) {
        const value = asRec(row)[key];
        if (typeof value === 'string' && value) return value;
      }
      return null;
    }, pickLabel: (row, ...keys) => {
      for (const key of keys) {
        const value = asRec(row)[key];
        if (typeof value === 'string' && value) return value;
      }
      return null;
    },
    unwrap: (body, ...keys) => {
      if (Array.isArray(body)) return body;
      for (const key of keys) {
        if (Array.isArray(asRec(body)[key])) return asRec(body)[key];
      }
      return [];
    },
  };
  const module = { exports: {} };
  const require = (id) => {
    if (id in extraRequires) return extraRequires[id];
    if (id === '../live' || id === './live') return live;
    if (id === '../client' || id === './client') {
      return {
        get: async () => ({}),
        post: async () => ({}),
        del: async () => ({}),
        mockOr: (_fixture, liveCall) => liveCall(),
        toQuery: () => '',
        isMock: false,
        ApiError: class ApiError extends Error {},
      };
    }
    if (id === '../marketRead' || id === './marketRead') {
      return { marketGet: async () => ({}) };
    }
    if (id.startsWith('@/mocks/') || id.includes('fixtures')) {
      return new Proxy({}, { get: () => () => [] });
    }
    return new Proxy({}, { get: () => () => undefined });
  };
  vm.runInNewContext(compiled, {
    module,
    exports: module.exports,
    require,
    URLSearchParams,
    Date,
    JSON,
    Math,
    Number,
    Object,
    Array,
    Map,
    Set,
    String,
    Boolean,
  });
  return module.exports;
}

/* ---------------- P1-02：期权链缺失字段保持 null ---------------- */

test('期权链缺失的报价与量持数据保持 null，不补 0', async () => {
  const chainBody = {
    ticker: 'AAOI',
    expiration: '2030-08-16',
    // underlying_price 缺失
    calls: [
      { strike: 25, bid: 1, ask: 1.2, volume: 300 }, // 无 oi / iv
      { bid: 2, ask: 2.2 }, // 无 strike —— 整条必须被丢弃
    ],
    puts: [{ strike: 30, bid: 0.9, ask: 1.1, open_interest: 40, volume: 5 }],
  };
  const { optionsApi } = loadModule('api/modules/options.ts', {
    '../marketRead': { marketGet: async () => chainBody },
  });
  const chain = await optionsApi.chain('AAOI', '2030-08-16');

  assert.equal(chain.spot, null, '标的现价缺失必须是 null，不是 0');
  assert.deepEqual(
    Array.from(chain.rows.map((row) => row.strike)),
    [25, 30],
    '行权价缺失的合约被丢弃，不生成 strike=0 的行',
  );

  const [call, put] = chain.rows;
  assert.equal(call.callVol, 300);
  assert.equal(call.callOi, null, '未平仓量缺失是 null');
  assert.equal(call.callIv, null, '隐含波动率缺失是 null');
  assert.equal(
    call.putBid,
    null,
    '只有看涨腿存在时，看跌侧保持 null，而不是一条全 0 腿',
  );
  assert.equal(put.putOi, 40);
  assert.equal(put.callVol, null);
});

test('期权适配层不再把缺失数值补成 0', async () => {
  const api = await source('api/modules/options.ts');
  const types = await source('api/types.ts');

  // legRow / mapChain / mapUnusual 内不得再出现 `?? 0`
  assert.doesNotMatch(codeOf(api), /\?\?\s*0/);
  // 行权价缺失的合约必须被丢弃，而不是归到 strike=0
  assert.match(api, /const strike = pickN\(c, 'strike'\);\s*\n\s*if \(strike === null\) continue;/);
  assert.match(api, /const strike = pickN\(p, 'strike'\);\s*\n\s*if \(strike === null\) continue;/);
  // 只有一侧存在时另一侧保持 null，不构造零值腿
  assert.match(api, /callOi: null, callVol: null, callIv: null, callBid: null, callAsk: null/);
  assert.match(api, /putOi: null, putVol: null, putIv: null, putBid: null, putAsk: null/);
  // 类型层同步可空，否则消费层不会被迫处理缺失
  assert.match(types, /callOi: number \| null/);
  assert.match(types, /spot: number \| null/);
});

test('缺买价或缺卖价时不产生 0 中间价', () => {
  assert.equal(midpoint(null, 1.2), null);
  assert.equal(midpoint(1.0, null), null);
  assert.equal(midpoint(0, 0), null, '双边为 0 不是可交易的中间价');
  assert.equal(midpoint(1.0, 1.2), 1.1);
  assert.equal(midpoint(0, 0.2), 0.1, '真实的 0 买价仍参与中间价');
});

/* ---------------- P1-03：持仓量为 0 的新开仓不再被过滤 ---------------- */

test('量持比四态：有比值 / 新开仓 / 无成交 / 不可用', () => {
  assert.deepEqual(volOiState(500, 100), { kind: 'ratio', ratio: 5 });
  assert.deepEqual(volOiState(500, 0), { kind: 'new_opening' });
  assert.deepEqual(volOiState(0, 0), { kind: 'no_activity' });
  assert.deepEqual(volOiState(500, null), { kind: 'unavailable' });
  assert.deepEqual(volOiState(null, 100), { kind: 'unavailable' });
});

test('持仓量为 0 且当日有成交的合约进入异动证据', () => {
  const row = {
    strike: 100,
    callOi: 0,
    callVol: 200,
    callIv: 0.5,
    callBid: 1,
    callAsk: 1.2,
    putOi: null,
    putVol: null,
    putIv: null,
    putBid: null,
    putAsk: null,
  };
  const evidence = buildOptionAlertEvidence(
    { ticker: 'AAOI', expiration: '2030-08-16', spot: 100, rows: [row] },
    '2030-08-16',
    21,
  );

  assert.equal(evidence.length, 1, '成交量 200 张、持仓量 0 的新挂牌合约必须被发现');
  const [alert] = evidence;
  assert.equal(alert.type, 'call');
  assert.equal(alert.open_interest, 0, '真实为 0 的持仓量要如实发送');
  assert.equal(
    alert.vol_oi_ratio,
    undefined,
    '持仓量为 0 时没有量持比，不能发送 0',
  );
  assert.ok(
    alert.reasons.some((reason) => reason.includes('新开仓')),
    `理由中应说明是新开仓，实际为 ${JSON.stringify(alert.reasons)}`,
  );
});

test('持仓量不可用时不发送 open_interest', () => {
  const row = {
    strike: 100,
    callOi: null,
    callVol: 4_000,
    callIv: null,
    callBid: 1,
    callAsk: 1.2,
    putOi: null,
    putVol: null,
    putIv: null,
    putBid: null,
    putAsk: null,
  };
  const [alert] = buildOptionAlertEvidence(
    { ticker: 'AAOI', expiration: '2030-08-16', spot: 100, rows: [row] },
    '2030-08-16',
    21,
  );

  assert.ok(alert, '成交量 4000 张应触发异动');
  assert.equal(
    'open_interest' in alert,
    false,
    '缺失的持仓量必须整字段省略，补 0 会被模型读成「确实没有持仓」',
  );
  assert.equal(alert.vol_oi_ratio, undefined);
  assert.ok(alert.reasons.some((reason) => reason.includes('持仓量不可用')));
});

test('标的现价缺失时价内外判定为 unavailable，而不是落在 0 上', () => {
  const row = {
    strike: 100,
    callOi: 10,
    callVol: 6_000,
    callIv: null,
    callBid: 1,
    callAsk: 1.2,
    putOi: null,
    putVol: null,
    putIv: null,
    putBid: null,
    putAsk: null,
  };
  const [alert] = buildOptionAlertEvidence(
    { ticker: 'AAOI', expiration: '2030-08-16', spot: null, rows: [row] },
    '2030-08-16',
    21,
  );
  assert.equal(alert.moneyness, 'unavailable');
});

/* ---------------- P1-13：读不到服务状态不等于服务正常 ---------------- */

test('后端未报告 worker 时扫描服务状态为未知，不是正常', () => {
  const { normalizeBreakoutStatus } = loadModule('api/modules/breakouts.ts');

  const unread = normalizeBreakoutStatus({
    status: 'unavailable',
    worker: null,
    latest_completed_scan: null,
  });
  assert.equal(
    unread.worker.healthy,
    null,
    '库不可用时后端回 worker: null，界面不能显示「扫描服务正常」',
  );

  const healthy = normalizeBreakoutStatus({
    worker: { health_status: 'ok', heartbeat_at: '2026-07-25T10:00:00Z' },
  });
  assert.equal(healthy.worker.healthy, true);

  const failed = normalizeBreakoutStatus({
    worker: { health_status: 'failed', heartbeat_at: '2026-07-25T10:00:00Z' },
  });
  assert.equal(failed.worker.healthy, false);
});

test('突破页把扫描服务画成三态', async () => {
  const page = await source('pages/Breakouts.tsx');
  assert.match(page, /status\.worker\?\.healthy === true[\s\S]{0,200}'正常'/);
  assert.match(page, /'状态未知'/);
});

test('催化状态读取失败显示读取失败，不显示采集暂停或 0 组热点', async () => {
  const hero = await source('components/catalysts/StatusHero.tsx');
  assert.match(hero, /const statusState = remoteState\(statusQ\)/);
  assert.match(hero, /statusUnread = statusState === 'error'/);
  assert.match(hero, /hotUnread = hotState === 'error'/);
  assert.match(hero, /状态读取失败/);
  // 三个格子都必须先看读取失败，再看业务值
  assert.match(hero, /\) : statusUnread \? \(\s*\n\s*unreadCell/);
  assert.match(hero, /\) : hotUnread \? \(\s*\n\s*unreadCell/);
});

/* ---------------- P2-9：时段读不到不等于休市 ---------------- */

test('市场时段读不到时显示时段未知', async () => {
  const page = await source('pages/Market.tsx');
  assert.doesNotMatch(codeOf(page), /status\?\.market \?\? 'closed'/);
  assert.match(page, /时段未知/);
  assert.match(page, /时段读取中/);
});

/* ---------------- P2-4 / P2-8：平盘是第三种状态 ---------------- */

test('平盘不计入上涨家数', async () => {
  const watchlist = await source('pages/Watchlist.tsx');
  assert.doesNotMatch(codeOf(watchlist), /changePct >= 0/);
  assert.match(watchlist, /advancers: known\.filter\(\(x\) => x\.changePct > 0\)/);
  assert.match(watchlist, /unchanged: known\.filter\(\(x\) => x\.changePct === 0\)/);
});

test('ChangeBadge 与指数跑马灯都有中性平盘态', async () => {
  const badge = await source('components/shared/ChangeBadge.tsx');
  const tape = await source('components/IndexTape.tsx');
  const reading = await source('components/market/SignalsReading.tsx');

  assert.doesNotMatch(codeOf(badge), /value >= 0/);
  assert.match(badge, /value > 0 \? 'up' : value < 0 \? 'down' : 'flat'/);
  assert.match(badge, /持平/);
  assert.match(badge, /name=\{[\s\S]{0,160}'minus'/);

  assert.doesNotMatch(codeOf(tape), /q\.changePct >= 0/);
  assert.match(tape, /q\.changePct > 0 \? 'up' : q\.changePct < 0 \? 'down' : 'flat'/);

  assert.doesNotMatch(codeOf(reading), /q\.changePct >= 0/);
  assert.match(reading, /indices\.filter\(\(q\) => q\.changePct > 0\)/);
});

/* ---------------- P2-14 / P2-15：缺失值不参与排序、不填成 0 分 ---------------- */

test('按涨跌幅排序时缺失值稳定排在末尾', async () => {
  const strength = await source('api/modules/strength.ts');
  assert.doesNotMatch(codeOf(strength), /\(a\.changePct \?\? 0\)/);
  assert.match(strength, /if \(left === null\) return 1;/);
  assert.match(strength, /if \(right === null\) return -1;/);
});

test('缺失分项评分不被填成 0 分', async () => {
  const strength = await source('api/modules/strength.ts');
  const types = await source('api/types.ts');
  assert.doesNotMatch(codeOf(strength), /trend: dims\[2\]\.value \?\? 0/);
  assert.match(strength, /trend: dims\[2\]\.value,/);
  assert.match(types, /trend: number \| null;/);
});

/* ---------------- P2-33：到期天数按纽约日历日 ---------------- */

test('到期天数不使用浏览器本地时区', async () => {
  const panel = await source('components/detail/OptionsPanel.tsx');
  assert.doesNotMatch(codeOf(panel), /T16:00:00/);
  assert.match(panel, /timeZone: 'America\/New_York'/);
  assert.match(panel, /T00:00:00Z/);
});
