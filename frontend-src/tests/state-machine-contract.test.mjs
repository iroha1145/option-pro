/**
 * 状态机与「数据时间 vs 请求时间」回归
 * （GPT-5.6-Pro 审计 P2-5 / P2-6 / P2-7 / P2-11 / P2-13 / P2-16 / P2-17 / P2-18 /
 *  P2-19 / P2-20 / P2-21 / P2-22 / P2-23 / P2-24 / P2-26 / P2-27 / P2-28 /
 *  P2-29 / P2-30 / P2-31 / P2-32）
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');

async function source(relativePath) {
  return readFile(path.join(src, relativePath), 'utf8');
}

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

/* ---------------- P2-5：闪烁不会永久残留 ---------------- */

test('闪烁定时器由独立 ref 持有，每轮数据先复位再决定是否重启', async () => {
  const hook = codeOf(await source('hooks/useTickFlash.ts'));
  // 定时器不再是 effect 的 cleanup：那正是「下一轮没有变化就没人清除」的原因
  assert.match(hook, /const timer = useRef<ReturnType<typeof setTimeout> \| null>\(null\)/);
  assert.match(hook, /if \(timer\.current !== null\) \{\s*\n\s*clearTimeout\(timer\.current\);/);
  assert.match(hook, /setFlashes\(\(current\) =>/);
  assert.match(hook, /if \(Object\.keys\(next\)\.length > 0\)/);
});

test('四处闪烁实现收敛到同一个 hook', async () => {
  for (const file of [
    'components/IndexTape.tsx',
    'pages/Watchlist.tsx',
    'pages/Breakouts.tsx',
  ]) {
    const text = codeOf(await source(file));
    assert.match(text, /useTickFlash\(/, `${file} 应使用共享 hook`);
    assert.doesNotMatch(
      text,
      /setTimeout\(\(\) => setFlashes\(\{\}\), 700\)/,
      `${file} 仍保留旧的内联闪烁定时器`,
    );
  }
});

/* ---------------- P2-6 / P2-7：自选页 ---------------- */

test('桌面表格提供删除入口，且不会误触打开详情', async () => {
  const page = codeOf(await source('pages/Watchlist.tsx'));
  assert.match(page, /从自选移除 \{ticker\}', \{ ticker: r\.ticker \}/);
  assert.match(page, /event\.stopPropagation\(\);\s*\n\s*void onRemoveTicker\(r\.ticker\);/);
});

test('消费 force 参数只删除 force 键', async () => {
  const page = codeOf(await source('pages/Watchlist.tsx'));
  assert.doesNotMatch(page, /setSearchParams\(\{\}, \{ replace: true \}\)/);
  assert.match(page, /next\.delete\('force'\);/);
});

/* ---------------- P2-11 / P2-13：选股页 ---------------- */

test('评分说明按实际使用的评分风格查找', async () => {
  const page = codeOf(await source('pages/Screener.tsx'));
  assert.doesNotMatch(page, /profiles\.find\(\(p\) => p\.id === applied\.presetId\) \?\? profiles\[0\]/);
  assert.match(page, /profiles\.find\(\(p\) => p\.id === applied\.profile\)/);
});

test('技术信号失败不再被缓存成「没有信号」', async () => {
  const page = codeOf(await source('pages/Screener.tsx'));
  const types = codeOf(await source('components/screener/types.ts'));
  assert.match(types, /export type RowSignalsState =/);
  assert.match(types, /\{ state: 'error' \}/);
  assert.doesNotMatch(page, /\[next\]: \[\] \}/);
  assert.match(page, /\[next\]: \{ state: 'error' \}/);
});

/* ---------------- P2-16：数据时间不是请求时间 ---------------- */

test('突破快照时间取契约 as_of，读取时间单独显示', async () => {
  const page = codeOf(await source('pages/Breakouts.tsx'));
  const api = codeOf(await source('api/modules/breakouts.ts'));
  assert.match(api, /currentEnvelope:/);
  assert.match(api, /asOf: pickS\(asRec\(d\), 'as_of', 'asOf'\)/);
  assert.match(page, /snapshotAt = currentQ\.data\?\.asOf/);
  assert.match(page, /\{__t\('数据截至'\)\} \{snapshotAt\} \{__t\('· 读取'\)\} \{readAt\}/);
});

/* ---------------- P2-17 / P2-18：只看自选 ---------------- */

test('只看自选同时作用于当前卡片与历史事件，且自选未就绪时不假装为空', async () => {
  const page = codeOf(await source('pages/Breakouts.tsx'));
  assert.match(page, /const matchWatch = \(ticker: string\) => !onlyWatch \|\| !watchReady \|\| watchSet\.has\(ticker\)/);
  assert.match(page, /events\.filter\(\(e\) => matchFilters\(e\) && matchWatch\(e\.ticker\)\)/);
  assert.match(page, /watchFilterPending = onlyWatch && !watchReady/);
});

/* ---------------- P2-19 / P2-20 / P2-24：分页与失败可见 ---------------- */

test('历史事件不再拼造总数，并可向服务端续读', async () => {
  const api = codeOf(await source('api/modules/breakouts.ts'));
  const rail = codeOf(await source('components/breakouts/HistoryRail.tsx'));
  assert.doesNotMatch(api, /page \* pageSize \+ 1/);
  assert.match(api, /total: pickN\(asRec\(d\), 'total', 'total_count'\)/);
  assert.match(api, /hasMore: nextCursor !== null/);
  assert.match(rail, /total: number \| null;/);
  assert.match(rail, /serverHasMore/);
  assert.match(rail, /继续读取更早事件/);
});

test('详情与加载更多的失败都会显示出来', async () => {
  const page = codeOf(await source('pages/Breakouts.tsx'));
  const detail = codeOf(await source('components/breakouts/EventDetail.tsx'));
  const feed = codeOf(await source('components/catalysts/FeedPanel.tsx'));
  assert.doesNotMatch(page, /\.catch\(\(\) => undefined\);/);
  assert.match(detail, /补充详情未能加载/);
  assert.doesNotMatch(feed, /catch \{\s*\n\s*\} finally/);
  assert.match(feed, /\{__t\('加载更多失败：'\)\}\{moreError\.message\}/);
});

/* ---------------- P2-21 / P2-22 / P2-23：催化页 ---------------- */

test('页头刷新覆盖顶部三块，而不只是标签内容', async () => {
  const page = codeOf(await source('pages/Catalysts.tsx'));
  assert.match(page, /<StatusHero refreshToken=\{refreshToken\} \/>/);
  assert.match(page, /<HotspotsStrip onOpenNews=\{setSelectedNewsId\} refreshToken=\{refreshToken\} \/>/);
  assert.match(page, /<FocusCycleCard refreshToken=\{refreshToken\} \/>/);
  for (const file of [
    'components/catalysts/StatusHero.tsx',
    'components/catalysts/HotspotsStrip.tsx',
    'components/catalysts/FocusCycleCard.tsx',
  ]) {
    assert.match(codeOf(await source(file)), /\[refreshToken\]\)/, `${file} 未把令牌纳入依赖`);
  }
});

test('请求失败不再更新「最后更新时间」', async () => {
  const page = codeOf(await source('pages/Catalysts.tsx'));
  const feed = codeOf(await source('components/catalysts/FeedPanel.tsx'));
  assert.match(page, /if \(result\.ok\) setLastLoadedAt\(Date\.now\(\)\)/);
  assert.match(feed, /onFeedResult\(\{ total: null, ok: false \}\)/);
  assert.match(feed, /onFeedResult\(\{ total: res\.total, ok: true \}\)/);
});

test('URL 筛选参数逐项校验，NaN 与非法枚举被拒绝', async () => {
  const page = await source('pages/Catalysts.tsx');
  const code = codeOf(page);
  assert.doesNotMatch(code, /as '' \| NewsClassification/);
  assert.doesNotMatch(code, /as '' \| NewsAnalysisStatus/);
  assert.match(code, /if \(!Number\.isFinite\(value\)\) return fallback;/);
  assert.match(code, /oneOf\(sp\.get\('cls'\), CLASSIFICATIONS\)/);
  assert.match(code, /oneOf\(sp\.get\('status'\), ANALYSIS_STATUSES\)/);
});

/* ---------------- P2-26 / P2-27 / P2-28：宏观面板 ---------------- */

test('手动刷新会被跟进到落地或超时，不会一直停在队列中', async () => {
  const panel = codeOf(await source('components/market/macro/MacroConditionsPanel.tsx'));
  assert.match(panel, /REFRESH_FOLLOW_INTERVAL_MS/);
  assert.match(panel, /REFRESH_FOLLOW_TIMEOUT_MS/);
  assert.match(panel, /if \(snapshotStamp !== refreshBaseline\)/);
});

test('历史接口失败与「暂无历史」分开呈现', async () => {
  const chart = codeOf(await source('components/market/macro/MacroHistoryChart.tsx'));
  const panel = codeOf(await source('components/market/macro/MacroConditionsPanel.tsx'));
  assert.match(chart, /error && points\.length === 0 \?/);
  assert.match(chart, /历史数据读取失败/);
  assert.match(panel, /error=\{historyQ\.error\}/);
});

test('因子详情缓存绑定快照标识', async () => {
  const details = codeOf(await source('components/market/macro/FactorDetails.tsx'));
  const panel = codeOf(await source('components/market/macro/MacroConditionsPanel.tsx'));
  assert.match(details, /snapshotKey/);
  assert.match(details, /if \(cachedSnapshot !== snapshotKey\) \{/);
  assert.match(details, /setStates\(\{\}\);/);
  assert.match(panel, /<FactorDetails modules=\{data\.modules\} snapshotKey=/);
});

/* ---------------- P2-29 / P2-30 / P2-31 / P2-32：财报与个股详情 ---------------- */

test('自动选中的财报股票消失后会重选，手动选择不受影响', async () => {
  const page = codeOf(await source('pages/Earnings.tsx'));
  assert.match(page, /selectedTicker === autoPickedTicker/);
  assert.match(page, /!items\.some\(\(it\) => it\.ticker === selectedTicker\)/);
});

test('自动轮询失败且有旧数据时显示过期横幅', async () => {
  const page = codeOf(await source('pages/Earnings.tsx'));
  assert.match(page, /refreshStatus !== 'failed_stale' && q\.error && items\.length > 0/);
  assert.match(page, /自动刷新失败，当前显示的是上一次的数据/);
});

test('缺股票代码或财报日期的行被丢弃', async () => {
  const api = codeOf(await source('api/modules/earnings.ts'));
  assert.doesNotMatch(api, /date: pickS\(r, 'date', 'earnings_date'\) \?\? ''/);
  assert.match(api, /if \(!rowTicker \|\| !rowDate\) return;/);
});

test('可选的强度补充有独立截止时间，不拖住核心详情', async () => {
  const api = codeOf(await source('components/detail/api.ts'));
  assert.match(api, /STRENGTH_SUPPLEMENT_DEADLINE_MS/);
  assert.match(api, /Promise\.race\(\[/);
});

test('闪烁 hook 不会因不稳定的入参引用陷入无限重渲染', async () => {
  const hook = codeOf(await source('hooks/useTickFlash.ts'));
  const page = codeOf(await source('pages/Breakouts.tsx'));
  // 两道防线：调用方记忆化入参，hook 自身在「本来就没有闪烁」时不写状态。
  assert.match(page, /const currentAll = useMemo\(/);
  assert.match(hook, /setFlashes\(\(current\) =>/);
  assert.match(hook, /Object\.keys\(current\)\.length === 0 \? current : next/);
});
