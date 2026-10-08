/**
 * 第二版菜单与文案：条件选股、突破雷达两页的结构约定（源码级）。
 * 只钉住「控件放在哪里、什么状态下出现」，不重复各页的行为测试；
 * 文案本身由词典覆盖率测试与浏览器用例覆盖。
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

test('显示数量在「更多条件」展开区里，折叠摘要仍写出返回上限', async () => {
  const workbench = codeOf(await source('components/screener/FilterWorkbench.tsx'));
  /* indexOf 找不到会返回 -1，slice 随之切出空串或错位片段，后面的 doesNotMatch 就空过了：先卡住标记在不在。 */
  const rowStart = workbench.indexOf('<motion.div variants={row}');
  const detailsStart = workbench.indexOf('<details');
  assert.ok(rowStart >= 0, '找不到主行起点 <motion.div variants={row}：标记改名后要同步本测试');
  assert.ok(detailsStart >= 0, '找不到「更多条件」展开区 <details：标记改名后要同步本测试');
  assert.ok(rowStart < detailsStart, '主行应在「更多条件」展开区之前');
  const mainRow = workbench.slice(rowStart, detailsStart);
  assert.match(mainRow, /<ScanButton/, '切到的确实是主行：扫描按钮在里面');
  assert.doesNotMatch(mainRow, /TOPN_OPTIONS|最多显示数量|显示数量/, '显示数量不能留在主行');
  const advanced = workbench.slice(detailsStart);
  assert.match(advanced, /data-screener-field="top-n"/);
  assert.match(advanced, /ariaLabel=\{__t\("最多显示数量"\)\}/);
  assert.match(advanced, /options=\{TOPN_OPTIONS\}/);
  assert.match(workbench, /TOPN_OPTIONS\.find\(\(option\) => option\.value === draft\.topN\)/);
  assert.match(workbench, /volumeSummary,\s*topNSummary,/, '摘要必须无条件包含显示数量');
});

test('无结果的恢复操作只在结果空态里，只列出已生效的限制', async () => {
  const page = codeOf(await source('pages/Screener.tsx'));
  assert.doesNotMatch(page, /AnimatePresence|key="relax"/, '侧栏不再有重复的「调整条件」卡');
  assert.match(page, /\(applied\.tier !== 'all' \|\| applied\.minScore != null\) && \(/);
  assert.match(page, /applied\.sectors\.length > 0 && <SuggestButton/);
  assert.match(page, /\(applied\.priceMin != null \|\| applied\.priceMax != null\) && \(/);
  // 成交额限制只有服务端确认支持才生效；没生效就不给清除按钮。
  assert.match(page, /appliedDollarVolumeFilterSupported && applied\.minDollarVol > 0 && <SuggestButton/);
});

test('单股诊断默认收起；展开状态只管输入区，结果与错误不在收起的面板里', async () => {
  const diagnostics = codeOf(await source('components/screener/SecurityDiagnostics.tsx'));
  assert.match(diagnostics, /const \[open, setOpen\] = useState\(false\)/);
  assert.match(diagnostics, /aria-expanded=\{open\}/);
  assert.match(diagnostics, /<div id=\{panelId\} hidden=\{!open\}>/);
  assert.match(diagnostics, /<\/form>\s*<\/div>\s*\{loading &&/, '结果、错误与加载提示必须在 hidden 面板之外');
});

test('突破雷达：状态收成一个下拉，最低评分与排序收进更多筛选，摘要写明四项当前条件', async () => {
  const page = codeOf(await source('pages/Breakouts.tsx'));
  assert.match(page, /<MenuSelect\s+ariaLabel=\{__t\('状态筛选'\)\}\s+value=\{statusFilter\}/);
  assert.doesNotMatch(page, /STATUS_CAPS\.map/, '七个状态按钮不再平铺');
  /* 只数 STATUS_CAPS 声明块里的选项：整页里别处也有大写取值的选项，数整页会把它们一并算进来。 */
  const capsStart = page.indexOf('const STATUS_CAPS');
  assert.ok(capsStart >= 0, '找不到 STATUS_CAPS 声明：改名后要同步本测试');
  const capsEnd = page.indexOf('];', capsStart);
  assert.ok(capsEnd > capsStart, '找不到 STATUS_CAPS 声明的结尾 ];');
  const statusValues = Array.from(
    page.slice(capsStart, capsEnd).matchAll(/\{ value: '([A-Z]+)', label: __t\(/g),
    (match) => match[1],
  );
  assert.equal(statusValues.length, 7, '七个状态选项一个不少');
  assert.deepEqual(
    [...statusValues].sort(),
    ['ALL', 'CONFIRMED', 'FAILED', 'HOLDING', 'RETESTING', 'TRIGGERED', 'WATCHING'],
    '七个取值各一个，不重不漏',
  );
  const moreStart = page.indexOf('data-testid="breakout-more-filters"');
  assert.ok(moreStart >= 0, '找不到 data-testid="breakout-more-filters"：标记改名后要同步本测试');
  const more = page.slice(moreStart);
  assert.match(more, /SCORE_CAPS\.map/);
  assert.match(more, /options=\{SORT_OPTIONS\}/);
  assert.match(page, /const filterSummary: \[string, string\]\[\] = \[/);
  for (const label of ['范围', '状态', '最低评分', '排序']) {
    assert.match(page, new RegExp(`\\[__t\\('${label}'\\),`), `摘要缺少「${label}」`);
  }
});

test('扫描触发钮只对管理员显示：重算评分与立即扫描', async () => {
  const screener = codeOf(await source('pages/Screener.tsx'));
  assert.match(screener, /\{isOwner && \(\s*<button\s+onClick=\{\(\) => void onStrengthRefresh\(\)\}/);
  const radar = codeOf(await source('pages/Breakouts.tsx'));
  assert.match(radar, /\{isOwner && \(\s*<button\s+onClick=\{onRefreshSnapshot\}/);
});
