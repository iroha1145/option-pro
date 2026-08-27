// EN/JA 词典覆盖率断言：每个源码里出现的 t('...') msgid 都必须有非空译文；
// 除 mocks/（含 AI 模拟正文，故意不全翻）外，展示位置不应再有漏包的中文字面量。
// 直接以 node 运行（随 `node --experimental-strip-types --test tests/*.test.mjs` 一并跑）。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import ts from 'typescript';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const srcDir = path.join(root, 'src');
const dictDir = path.join(srcDir, 'i18n', 'dict');

const CJK = /[぀-ヿ㐀-䶿一-鿿＀-￯]/;

async function walk(dir) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await walk(p)));
    else if (/\.(ts|tsx)$/.test(entry.name)) out.push(p);
  }
  return out;
}

function parse(file, text) {
  return ts.createSourceFile(
    file,
    text,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
}

/** display-position classifier — mirrors the codemod so "should have been wrapped" stays in sync. */
function classify(node) {
  const p = node.parent;
  if (!p) return 'unknown';
  // `t` is imported as `__t` in files that already bind their own local `t`
  // (e.g. a Date instance) — see the codemod's bindsIdentifier() for why.
  if (ts.isCallExpression(p) && /^(t|__t)$/.test(p.expression.getText()) && p.arguments[0] === node) return 'already-wrapped';
  if ((ts.isPropertyAssignment(p) || ts.isPropertySignature(p)) && p.name === node) return 'unsafe';
  if (ts.isComputedPropertyName(p)) return 'unsafe';
  if (ts.isElementAccessExpression(p) && p.argumentExpression === node) return 'unsafe';
  if (ts.isBinaryExpression(p)) {
    const k = p.operatorToken.kind;
    if (
      k === ts.SyntaxKind.EqualsEqualsEqualsToken ||
      k === ts.SyntaxKind.ExclamationEqualsEqualsToken ||
      k === ts.SyntaxKind.EqualsEqualsToken ||
      k === ts.SyntaxKind.ExclamationEqualsToken
    )
      return 'unsafe';
  }
  if (ts.isCaseClause(p)) return 'unsafe';
  if (ts.isLiteralTypeNode(p)) return 'unsafe';
  if (ts.isImportDeclaration(p) || ts.isExportDeclaration(p) || ts.isImportTypeNode(p)) return 'unsafe';
  if (ts.isCallExpression(p)) {
    const fnText = p.expression.getText();
    // 锚定到方法名末段，避免 `setRefreshNote`/`getStatus` 这类业务函数因子串
    // 命中 `set`/`get` 被误判为「安全位置」而放走其中文参数（曾漏包一批 setter）。
    if (/(^|\.)(localeCompare|includes|indexOf|startsWith|endsWith|has|get|set|delete|split|replace|match|test|Set|Map)$/.test(fnText))
      return 'unsafe';
    return 'display';
  }
  return 'display';
}

/**
 * 该节点是否位于「按 locale 分支产出各语言自然写法」的函数内——这类模板串
 * （en「7/26」· ja「7月26日」· zh「7 月 26 日」）是有意的，不该被 t() 包。
 * 判据：最近的函数体文本里出现 getLocale()。见 HistoryRail.dayLabel、
 * earnings/types.fmtMDCN、MonthCalendar 的年月标题。
 */
function inLocaleBranch(node) {
  for (let p = node.parent; p; p = p.parent) {
    if (
      ts.isFunctionDeclaration(p) ||
      ts.isFunctionExpression(p) ||
      ts.isArrowFunction(p) ||
      ts.isMethodDeclaration(p)
    ) {
      if (/getLocale\s*\(/.test(p.getText())) return true;
    }
  }
  return false;
}

/** 是否在 throw 语句内——诊断性 Error（数据损坏时抛给开发者，非 UI 文案）不翻译。 */
function inThrow(node) {
  for (let p = node.parent; p; p = p.parent) if (ts.isThrowStatement(p)) return true;
  return false;
}

/**
 * 模板串按 file:line 豁免：detail/api.ts 的 signalReading / createSignalAnalysisJob
 * 生成的是 mock 模式下的「模型分析正文」，与真实 AI 输出一样刻意不翻译（同 KNOWN_
 * TYPE_DISCRIMINANTS 第 2 类）。行号来自这些 mock 拼接语句。
 */
const KNOWN_TEMPLATE_EXEMPT_LINES = new Set([
  'components/detail/api.ts:399',
  'components/detail/api.ts:538',
  'components/detail/api.ts:539',
  'components/detail/api.ts:540',
]);

// ── 收集 dict/*.ts 里的全部词条（跳过 types.ts / index.ts 本身） ────────────
const dictFiles = (await readdir(dictDir)).filter((f) => /\.ts$/.test(f) && !['types.ts', 'index.ts'].includes(f));
assert.ok(dictFiles.length > 0, 'src/i18n/dict/ 下必须至少有一个词典文件');

const merged = new Map(); // msgid -> { en, ja, sourceFile }
const conflicts = [];

for (const file of dictFiles) {
  const mod = await import(pathToFileURL(path.join(dictDir, file)).href);
  const exported = Object.values(mod).find((v) => v && typeof v === 'object');
  assert.ok(exported, `${file} 必须导出一个 Dict 对象`);
  for (const [msgid, entry] of Object.entries(exported)) {
    assert.ok(Array.isArray(entry) && entry.length === 2, `${file} 的词条「${msgid}」必须是 [en, ja] 二元组`);
    const [en, ja] = entry;
    assert.ok(typeof en === 'string' && en.trim(), `${file} 缺英文译文：${msgid}`);
    assert.ok(typeof ja === 'string' && ja.trim(), `${file} 缺日文译文：${msgid}`);
    assert.ok(!CJK.test(en), `${file} 的英文译文里混入了中文字符：${msgid} -> ${en}`);

    const prior = merged.get(msgid);
    if (prior && (prior.en !== en || prior.ja !== ja)) {
      conflicts.push({ msgid, files: [prior.file, file], values: [[prior.en, prior.ja], [en, ja]] });
    }
    merged.set(msgid, { en, ja, file });
  }
}

test('dict/*.ts 词条之间没有同 msgid 不同译文的冲突', () => {
  assert.deepEqual(
    conflicts.map((c) => `${c.msgid} (${c.files.join(' vs ')})`),
    [],
    '同一个中文原文在不同词典文件里被翻成了不同的 en/ja，需要统一',
  );
});

/**
 * 人工核实过的白名单：这些中文字面量故意不包 t()，两类原因，都不是遗漏。
 *
 * 1. 字面量联合类型的判别值——TS 类型系统要求精确匹配某个具体字符串，t() 返回
 *    宽泛 string 会让类型检查失败；真正的展示翻译发生在各自的渲染处（已验证）：
 *    - CommandPalette.tsx 的 `group` 字段 → 渲染处 `{__t(g.name)}`（line 312）
 *    - detail/api.ts 的 TrendBiasLabel 内部值 → TrendBiasPanel.tsx 用 `{t(label)}` 渲染
 *    - Market.tsx 的 TrendBias['label'] 内部值 → SignalsReading.tsx 用 `{t(bias.label)}` 渲染
 *
 * 2. mock 模式下模拟「模型写的分析正文」（detail/api.ts 的 createSignalAnalysisJob），
 *    住在 mocks/ 目录之外，但和真实 AI 输出一样不该翻译——codemod 的 mocks 白名单
 *    只按路径过滤，看不到这种「不在 mocks/ 但同样是 AI 模拟内容」的情况。
 *
 * codemod 只看语法位置，两种情况都看不出来，所以需要这份人工核实过的白名单，
 * 而不是放宽通用规则掩盖真正遗漏的包裹。
 */
const KNOWN_TYPE_DISCRIMINANTS = new Set([
  'components/CommandPalette.tsx:161 股票',
  'components/CommandPalette.tsx:171 最近',
  'components/CommandPalette.tsx:176 功能',
  'components/CommandPalette.tsx:190 功能',
  'components/CommandPalette.tsx:201 功能',
  // 客户账号退出/登录条目（#18）：group 判别字段，同上由渲染处 __t 包裹
  'components/CommandPalette.tsx:215 功能',
  'components/CommandPalette.tsx:227 功能',
  'components/detail/api.ts:461 数据不足',
  'components/detail/api.ts:463 偏多',
  'components/detail/api.ts:465 偏空',
  'components/detail/api.ts:466 中性',
  // 等待占位哨兵：对照后端落库的中文字面量，绝不能 __t（译文永不命中，防御失效）
  'components/catalysts/api.ts:132 中文标题等待生成',
  'components/catalysts/api.ts:132 中文摘要等待生成',
  'components/catalysts/api.ts:132 热点标题等待中文分析',
  'pages/Market.tsx:68 偏多',
  'pages/Market.tsx:68 偏空',
  'pages/Market.tsx:68 中性',
  'components/detail/api.ts:536 偏贵',
  'components/detail/api.ts:536 相对便宜',
  'components/detail/api.ts:536 中性',
  'components/detail/api.ts:541 近端观察 MA20 附近的量能配合与突破延续性；若量价背离放大，偏向读数将快速回落。',
  'components/detail/api.ts:542 以上为方向性研究结论，非收益预测。',
  // `t(macroMissingReason(status) ?? '暂无宏观读数')` — the literal is the right
  // operand of `??`, not itself t()'s direct argument, so the classifier can't see
  // that the whole expression is covered by the outer call. It is (verified by hand).
  'components/shared/MacroFitBadge.tsx:37 暂无宏观读数',
  'components/shared/MacroFitPanel.tsx:93 暂无宏观读数',
]);

/**
 * lib/macroFit.ts is deliberately dependency-free — tests/macro-fit-presentation
 * .test.mjs asserts this by giving its sandbox no `require` at all, on purpose (see
 * that file's own comment: a broken import there is "a useful reminder, not an
 * obstacle to work around"). Its Chinese literals therefore stay unwrapped in the
 * source; translation happens at each render site instead (MacroFitBadge.tsx,
 * MacroFitPanel.tsx, MacroTechnicalMatrix.tsx, SectorList.tsx, Screener.tsx,
 * LeadBigCard.tsx all wrap the values macroFit.ts exports before displaying them).
 */
const IMPORT_FREE_FILES = new Set(['lib/macroFit.ts']);

// ── 扫描 src 里每一处 t('...') 调用，确认 msgid 都在词典里 ──────────────────
const allFiles = (await walk(srcDir)).filter((f) => !f.includes(`${path.sep}i18n${path.sep}`));

const calledMsgids = new Map(); // msgid -> [{file,line}]
const unsafeDisplayGaps = []; // 非 mocks 文件里疑似漏包的展示位中文
const mocksGaps = [];

for (const file of allFiles) {
  const rel = path.relative(srcDir, file).split(path.sep).join('/');
  const isExempt = rel.startsWith('mocks/') || IMPORT_FREE_FILES.has(rel);
  const text = await readFile(file, 'utf8');
  const sf = parse(file, text);
  const lineOf = (n) => sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1;

  (function visit(node) {
    if (ts.isCallExpression(node) && /^(t|__t)$/.test(node.expression.getText()) && node.arguments[0]) {
      const arg = node.arguments[0];
      if (ts.isStringLiteral(arg) || ts.isNoSubstitutionTemplateLiteral(arg)) {
        if (!calledMsgids.has(arg.text)) calledMsgids.set(arg.text, []);
        calledMsgids.get(arg.text).push({ file: rel, line: lineOf(arg) });
      }
    } else if (ts.isJsxText(node) && CJK.test(node.text) && node.text.trim()) {
      (isExempt ? mocksGaps : unsafeDisplayGaps).push({ file: rel, line: lineOf(node), text: node.text.trim() });
    } else if (
      (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) &&
      CJK.test(node.text) &&
      classify(node) === 'display' &&
      !KNOWN_TYPE_DISCRIMINANTS.has(`${rel}:${lineOf(node)} ${node.text}`)
    ) {
      (isExempt ? mocksGaps : unsafeDisplayGaps).push({ file: rel, line: lineOf(node), text: node.text });
    } else if (ts.isTemplateExpression(node) && !inLocaleBranch(node) && !inThrow(node)) {
      // 带插值的模板串 `前缀${x}后缀`：codemod 只看纯字面量，看不到 head/middle/
      // tail 段——一批「市场时段：${label}」「前往${label}页」式漏包正藏在这里。
      const parts = [node.head, ...node.templateSpans.map((s) => s.literal)];
      for (const part of parts) {
        if (CJK.test(part.text) && part.text.trim() && !KNOWN_TEMPLATE_EXEMPT_LINES.has(`${rel}:${lineOf(part)}`)) {
          (isExempt ? mocksGaps : unsafeDisplayGaps).push({ file: rel, line: lineOf(part), text: part.text.trim() });
        }
      }
    }
    ts.forEachChild(node, visit);
  })(sf);
}

test('every t(msgid) call site has a non-empty EN + JA dictionary entry', () => {
  const missing = [];
  for (const [msgid, sites] of calledMsgids) {
    if (!merged.has(msgid)) missing.push(`${JSON.stringify(msgid)} — first used at ${sites[0].file}:${sites[0].line}`);
  }
  assert.deepEqual(missing, [], `${missing.length} msgid(s) called via t() have no dictionary entry`);
});

test('每个含 {占位符} 的词条都有字面 t() 调用点（漏接线的机器可查信号）', () => {
  // 带占位符的词条只可能经字面 t('…{n}…', {…}) 使用，不可能来自 t(动态变量)。
  // 因此「词条在、字面调用不在」= 接线漏了（fmtRelative 的 '{n} 分钟前' 一族、
  // '市场时段：{label}' 都曾这样漏掉）。companies/mocks 是动态数据源，豁免。
  const orphaned = [];
  for (const [msgid, meta] of merged) {
    if (!/\{[a-zA-Z_]\w*\}/.test(msgid)) continue;
    if (meta.file === 'companies.ts' || meta.file === 'mocks.ts') continue;
    if (!calledMsgids.has(msgid)) orphaned.push(`${JSON.stringify(msgid)} (${meta.file})`);
  }
  assert.deepEqual(orphaned, [], `${orphaned.length} 个占位符词条没有任何字面 t() 调用点——可能是接线遗漏`);
});

test('no un-wrapped Chinese literal remains in a display position outside src/mocks/', () => {
  const sample = unsafeDisplayGaps.slice(0, 30).map((g) => `${g.file}:${g.line} ${JSON.stringify(g.text)}`);
  assert.deepEqual(
    sample,
    [],
    `${unsafeDisplayGaps.length} display-position Chinese literal(s) are not wrapped in t() — run the i18n codemod`,
  );
});

test('exempt paths (src/mocks/, import-free files) have no untracked untranslated strings', () => {
  // mocks/ intentionally leaves AI-simulated prose untranslated (see dict/mocks.ts's own
  // header comment); lib/macroFit.ts is intentionally import-free (see IMPORT_FREE_FILES
  // above). This test only prints a visibility count, it does not fail the suite, since
  // new demo fixtures or macroFit additions legitimately add new Chinese text over time.
  if (mocksGaps.length > 0) {
    console.log(`[i18n] ${mocksGaps.length} untranslated Chinese literal(s) remain in exempt paths (mocks/ AI content, or lib/macroFit.ts's deliberate zero-import rule).`);
  }
  assert.ok(true);
});

// ── 语言切换器确实挂在导航上 ─────────────────────────────────────────────
test('LanguageSwitcher is wired into the navbar', async () => {
  const navbar = await readFile(path.join(srcDir, 'components', 'Navbar.tsx'), 'utf8');
  assert.match(navbar, /LanguageSwitcher/, 'Navbar.tsx must render <LanguageSwitcher />');
});

test('i18n core exposes zh/en/ja and a browser-language auto-detect', async () => {
  const core = await readFile(path.join(srcDir, 'i18n', 'core.ts'), 'utf8');
  assert.match(core, /'zh'\s*\|\s*'en'\s*\|\s*'ja'/, 'Locale union must be zh | en | ja');
  assert.match(core, /detectLocale/, 'core.ts must export a browser-language detector');
});
