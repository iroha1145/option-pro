/**
 * 宏观适配展示层（增量任务 Phase 1）。
 *
 * 这些测试围着一条断言组织：**没读到 ≠ 中性**。后端在覆盖度不足时返回 null 而不是
 * 50，前端任何一处把它兜成 50 或「中性」，这个设计就白做了。
 *
 * 另外一条：顺风/逆风的分界线在前后端各有一份代码，所以这里直接读后端的
 * linkage.py 断言两个数字相等 —— 不是把后端的值抄一遍再和自己比。
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import ts from 'typescript';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '..', '..');

/**
 * 编译并运行一个自包含的 .ts 模块。
 *
 * 没有给沙箱 require：macroFit.ts 刻意不依赖任何东西（纯展示规则），一旦有人给它
 * 加了导入，这里会立刻以 require is not defined 失败 —— 那是个有用的提醒，
 * 不是需要绕过的障碍。
 */
function loadModule(relativePath) {
  const source = readFileSync(resolve(repoRoot, relativePath), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(compiled, { module, exports: module.exports });
  return module.exports;
}

const macroFit = loadModule('frontend-src/src/lib/macroFit.ts');

/** 后端常量的字面值，直接从源码里读出来，不在这里抄一份。 */
function backendConstant(name) {
  const source = readFileSync(
    resolve(repoRoot, 'backend/app/services/macro_conditions/linkage.py'),
    'utf8',
  );
  const match = source.match(new RegExp(`^${name}\\s*=\\s*([0-9.]+)`, 'm'));
  assert.ok(match, `linkage.py 里找不到 ${name}`);
  return Number(match[1]);
}

/* ---------------- 分界线与后端同源 ---------------- */

test('顺风/逆风分界线与后端 linkage.py 完全一致', () => {
  assert.equal(
    macroFit.MACRO_TAILWIND_STRONG,
    backendConstant('TAILWIND_STRONG'),
    '前端的顺风线和后端不一样：同一个分数会在两处得到不同标签',
  );
  assert.equal(
    macroFit.MACRO_TAILWIND_WEAK,
    backendConstant('TAILWIND_WEAK'),
  );
});

test('后端的影子上限确实是 ±3 / ±4，界面文案照此说明', () => {
  assert.equal(backendConstant('STRENGTH_SHADOW_CAP'), 3);
  assert.equal(backendConstant('BREAKOUT_PRIORITY_SHADOW_CAP'), 4);
});

/* ---------------- 没读到 ≠ 中性 ---------------- */

test('缺失分数返回 null，而不是中性', () => {
  for (const value of [null, undefined, Number.NaN, Infinity, '58']) {
    assert.equal(
      macroFit.macroTone(value),
      null,
      `${String(value)} 被当成了一个分数`,
    );
  }
});

test('分档在边界上是闭区间，与后端的 >= / <= 一致', () => {
  assert.equal(macroFit.macroTone(65), 'tailwind');
  assert.equal(macroFit.macroTone(64.9), 'neutral');
  assert.equal(macroFit.macroTone(35), 'headwind');
  assert.equal(macroFit.macroTone(35.1), 'neutral');
  assert.equal(macroFit.macroTone(50), 'neutral');
});

test('后端标签优先，但不认得的标签不会被当成中性', () => {
  // 后端说逆风，分数在中性区间 —— 以后端为准，前端不二次判断。
  assert.equal(macroFit.macroToneOf(50, '逆风'), 'headwind');
  // 标签是噪声时回退到分数，而不是回退到「中性」。
  assert.equal(macroFit.macroToneOf(80, 'whatever'), 'tailwind');
  // 分数也没有时，什么都不给。
  assert.equal(macroFit.macroToneOf(null, 'whatever'), null);
  assert.equal(macroFit.macroToneOf(null, null), null);
});

test('三种缺失原因各有各的说法，不并成一句「暂无」', () => {
  const reasons = new Set(
    [
      'macro_snapshot_unavailable',
      'sector_unclassified',
      'exposure_coverage_low',
    ].map((status) => macroFit.macroMissingReason(status)),
  );
  assert.equal(reasons.size, 3, '不同原因给出了同一句话');
  for (const text of reasons) {
    assert.ok(text && text.length > 0);
  }
  assert.equal(macroFit.macroMissingReason('ok'), null, '正常状态不该有原因文案');
  assert.equal(macroFit.macroMissingReason(null), null);
  // 没见过的状态给一句通用的，而不是 undefined 打到界面上。
  assert.equal(typeof macroFit.macroMissingReason('brand_new_status'), 'string');
});

/* ---------------- 二维状态 ---------------- */

test('四个象限彼此不同，并且任一侧缺失就不给状态', () => {
  assert.equal(macroFit.macroQuadrant(70, 70), 'both_strong');
  assert.equal(macroFit.macroQuadrant(70, 30), 'price_ahead');
  assert.equal(macroFit.macroQuadrant(30, 70), 'macro_ahead');
  assert.equal(macroFit.macroQuadrant(30, 30), 'both_weak');
  // 缺一半不猜另一半。
  assert.equal(macroFit.macroQuadrant(null, 70), null);
  assert.equal(macroFit.macroQuadrant(70, null), null);
  assert.equal(macroFit.macroQuadrant(null, null), null);
});

test('每个象限都有自己的措辞，不重复', () => {
  const labels = Object.values(macroFit.MACRO_QUADRANT_LABEL);
  assert.equal(new Set(labels).size, labels.length);
  const hints = Object.values(macroFit.MACRO_QUADRANT_HINT);
  assert.equal(new Set(hints).size, hints.length);
  assert.equal(labels.length, 4);
});

test('差值的符号方向：正数是价格跑在环境前面', () => {
  assert.equal(macroFit.macroGap(80, 50), 30);
  assert.equal(macroFit.macroGap(50, 80), -30);
  assert.equal(macroFit.macroGap(50, 50), 0);
  assert.equal(macroFit.macroGap(null, 50), null);
  assert.equal(macroFit.macroGap(50, null), null);
});

/* ---------------- 驱动因素 ---------------- */

test('驱动因素用后端下发的中文名，缺名字才退回因子 id', () => {
  assert.equal(
    macroFit.driverText([
      { factor_id: 'fed_net_liquidity', label: '联储净流动性' },
      { factor_id: 'real_rate_level', label: '实际利率水平' },
    ]),
    '联储净流动性、实际利率水平',
  );
  // 标签缺失时显示 id 总比显示空白强：至少还能查。
  assert.equal(
    macroFit.driverText([{ factor_id: 'wti_oil', label: '' }]),
    'wti_oil',
  );
  assert.equal(macroFit.driverText([]), null, '空列表要让调用方决定不渲染');
  assert.equal(macroFit.driverText(null), null);
  assert.equal(macroFit.driverText(undefined), null);
});

test('前端没有第二份「因子 id → 中文名」映射表', () => {
  // 这样一份表是同一个事实的第二份拷贝：后端改名之后界面继续显示旧名字，
  // 而且什么都不会失败。中文名一律由后端随字段下发。
  const source = readFileSync(
    resolve(repoRoot, 'frontend-src/src/lib/macroFit.ts'),
    'utf8',
  );
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
  for (const factorId of [
    'fed_net_liquidity',
    'real_rate_level',
    'wti_oil',
    'hy_credit',
    'vix',
  ]) {
    assert.ok(
      !code.includes(factorId),
      `macroFit.ts 里出现了因子 id ${factorId}：中文名必须由后端下发`,
    );
  }
});

/* ---------------- 界面口径 ---------------- */

test('口径说明写明了三件必须说的事', () => {
  const hint = macroFit.MACRO_SHADOW_HINT;
  const all = `${hint.title}${hint.body}${hint.note}`;
  // 一个 0–100 的分数默认会被当成评分的一部分，所以这三句缺一不可。
  assert.match(all, /影子/, '没说这是影子字段');
  assert.match(all, /不参与排名/, '没说它不参与排名');
  assert.match(all, /板块/, '没说暴露画像是板块级的');
  assert.match(all, /不是预测/, '没说分数是历史分位而不是预测');
  assert.match(all, /不按中性 50 计|不按中性/, '没说缺失不按中性处理');
  // 原生 title 属性只能放字符串，两者内容一致。
  assert.equal(typeof macroFit.MACRO_SHADOW_TITLE_ATTR, 'string');
  assert.ok(macroFit.MACRO_SHADOW_TITLE_ATTR.includes(hint.body));
});

test('宏观筛选排除没有读数的行，而不是把它们当中性留下', () => {
  const source = readFileSync(
    resolve(repoRoot, 'frontend-src/src/pages/Screener.tsx'),
    'utf8',
  );
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
  assert.match(
    code,
    /macroToneOf\(r\.macroFit, r\.macroTailwind\) === macroToneFilter/,
    '筛选必须比较分档结果；null 分档不等于任何一档，因此自然被排除',
  );
  // 被排除的数量要说出来，否则看起来像那些股票不存在。
  assert.match(code, /macroUnreadCount/);
  // 列关掉时必须清掉筛选，否则行按一个看不见的条件被筛。
  assert.match(code, /if \(on\) setMacroToneFilter\('all'\)/);
});

test('宏观列默认关闭，且默认排序仍是原来的确定性排序', () => {
  const source = readFileSync(
    resolve(repoRoot, 'frontend-src/src/pages/Screener.tsx'),
    'utf8',
  );
  assert.match(source, /useState\(false\);?\s*$/m);
  assert.match(source, /useState<SortMode>\('deterministic'\)/);
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
  // 排序键里不能出现宏观字段：它是影子字段，不参与排名。
  assert.doesNotMatch(code, /out\.sort\([^)]*macro/i);
});

test('板块表默认排序没有被宏观列改掉', () => {
  const source = readFileSync(
    resolve(repoRoot, 'frontend-src/src/components/sectors/SectorList.tsx'),
    'utf8',
  );
  assert.match(source, /defaultSort=\{\{ key: 'avgReturn', desc: true \}\}/);
});

test('象限措辞不复用顺风/逆风：两套分界线不同，同屏会互相打脸', () => {
  // 象限在 50 分处切，顺风/逆风在 65 / 35 —— 一个 53 分的读数徽标显示「中性」，
  // 象限若同时说「宏观顺风」，同一个数字在同一屏上就有两种说法（实测 53.2 就是）。
  for (const label of Object.values(macroFit.MACRO_QUADRANT_LABEL)) {
    assert.doesNotMatch(label, /顺风|逆风/, `象限措辞「${label}」复用了分档的词`);
  }
  // 而分界口径本身要说出来。
  assert.match(macroFit.MACRO_QUADRANT_NOTE, /50/);
  assert.match(macroFit.MACRO_QUADRANT_NOTE, /65/);
  assert.match(macroFit.MACRO_QUADRANT_NOTE, /35/);
});

test('50 分正好落在象限的强侧，与顺风分档的中性不冲突', () => {
  assert.equal(macroFit.macroQuadrant(50, 50), 'both_strong');
  assert.equal(macroFit.macroTone(50), 'neutral');
  // 这两句同时为真才是对的：象限说的是「相对 50 的哪一边」，分档说的是「够不够强」。
});

test('驱动因素映射同时接受新旧两种形状（部署窗口）', () => {
  // 磁盘快照在 Worker 重跑之前仍是裸 id 数组。丢成空列表会让面板说
  // 「各宏观因子方向不明显」—— 那是一句关于数据的判断，不是「读不到名字」。
  const source = readFileSync(
    resolve(repoRoot, 'frontend-src/src/api/macroFields.ts'),
    'utf8',
  );
  const start = source.indexOf('export function mapMacroFitDrivers');
  assert.ok(start > 0);
  const body = source.slice(start, source.indexOf('\n}', start) + 2)
    .replace('export function', 'function');
  const compiled = ts.transpileModule(body, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const sandbox = {
    asRec: (v) => (v !== null && typeof v === 'object' && !Array.isArray(v) ? v : {}),
    pickS: (r, ...keys) => {
      for (const k of keys) if (typeof r[k] === 'string' && r[k]) return r[k];
      return null;
    }, pickLabel: (r, ...keys) => {
      for (const k of keys) if (typeof r[k] === 'string' && r[k]) return r[k];
      return null;
    },
    map: null,
  };
  vm.runInNewContext(`${compiled}; map = mapMacroFitDrivers;`, sandbox);
  const map = sandbox.map;

  // vm 里造出来的对象和这个 realm 的 Object 不同源，deepStrictEqual 会因此失败；
  // 比较 JSON 就绕开了 realm，比较的仍是实际内容。
  const plain = (value) => JSON.parse(JSON.stringify(value));

  // 新形状
  assert.deepEqual(plain(map([{ factor_id: 'wti_oil', label: 'WTI 原油' }])), [
    { factor_id: 'wti_oil', label: 'WTI 原油' },
  ]);
  // 旧形状：显示 id，那正是旧快照携带的全部信息
  assert.deepEqual(plain(map(['real_rate_level', 'wti_oil'])), [
    { factor_id: 'real_rate_level', label: 'real_rate_level' },
    { factor_id: 'wti_oil', label: 'wti_oil' },
  ]);
  // 垃圾输入不产出条目
  assert.deepEqual(plain(map(['', '  ', null, 42, {}])), []);
  assert.deepEqual(plain(map(null)), []);
  assert.deepEqual(plain(map('not-an-array')), []);
});

test('个股宏观适配读的是概览端点，不是只覆盖前 20 名的强度端点', () => {
  // /strength/stocks/{t} 只回答公开快照 top 切片里的代码，其余一律 404。拿它当
  // 主源时 AMD、SLB 等约 190 只票明明算得出分，抽屉上却写着「暂无宏观读数」。
  const stocks = readFileSync(
    resolve(repoRoot, 'frontend-src/src/api/modules/stocks.ts'),
    'utf8',
  );
  assert.match(stocks, /macroFit: pickN\(r, 'macro_fit_shadow'\)/,
    '概览映射没有读取 macro_fit_shadow');

  const detail = readFileSync(
    resolve(repoRoot, 'frontend-src/src/components/detail/api.ts'),
    'utf8',
  );
  const code = detail.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
  // 概览优先、扫描行兜底。这一条正好表达了意图：概览必须排在前面。
  assert.match(
    code,
    /macroFit: detail\.macroFit \?\? strength\?\.macroFit \?\? null/,
    '抽屉的宏观适配不是以概览为主源',
  );
});

test('mock 映射表不住在 lib/macroFit 之外的第二处', () => {
  // 驱动因素映射只有一份实现，四个消费方都从 api/macroFields 引入 ——
  // 挂在 strength.ts 上会让另外三个 chunk 为一个函数拖上 mock fixture。
  const shared = readFileSync(
    resolve(repoRoot, 'frontend-src/src/api/macroFields.ts'),
    'utf8',
  );
  assert.match(shared, /export function mapMacroFitDrivers/);
  const strength = readFileSync(
    resolve(repoRoot, 'frontend-src/src/api/modules/strength.ts'),
    'utf8',
  );
  assert.doesNotMatch(strength, /export function mapMacroFitDrivers/);
});
