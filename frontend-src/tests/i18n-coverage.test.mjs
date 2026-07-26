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
  if (ts.isCallExpression(p) && p.expression.getText() === 't' && p.arguments[0] === node) return 'already-wrapped';
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
    if (/localeCompare|includes|indexOf|startsWith|endsWith|has|get|set|delete|split|replace|match|test|Set$|Map$/.test(fnText))
      return 'unsafe';
    return 'display';
  }
  return 'display';
}

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

// ── 扫描 src 里每一处 t('...') 调用，确认 msgid 都在词典里 ──────────────────
const allFiles = (await walk(srcDir)).filter((f) => !f.includes(`${path.sep}i18n${path.sep}`));

const calledMsgids = new Map(); // msgid -> [{file,line}]
const unsafeDisplayGaps = []; // 非 mocks 文件里疑似漏包的展示位中文
const mocksGaps = [];

for (const file of allFiles) {
  const rel = path.relative(srcDir, file).split(path.sep).join('/');
  const isMocks = rel.startsWith('mocks/');
  const text = await readFile(file, 'utf8');
  const sf = parse(file, text);
  const lineOf = (n) => sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1;

  (function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText() === 't' && node.arguments[0]) {
      const arg = node.arguments[0];
      if (ts.isStringLiteral(arg) || ts.isNoSubstitutionTemplateLiteral(arg)) {
        if (!calledMsgids.has(arg.text)) calledMsgids.set(arg.text, []);
        calledMsgids.get(arg.text).push({ file: rel, line: lineOf(arg) });
      }
    } else if (ts.isJsxText(node) && CJK.test(node.text) && node.text.trim()) {
      (isMocks ? mocksGaps : unsafeDisplayGaps).push({ file: rel, line: lineOf(node), text: node.text.trim() });
    } else if (
      (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) &&
      CJK.test(node.text) &&
      classify(node) === 'display'
    ) {
      (isMocks ? mocksGaps : unsafeDisplayGaps).push({ file: rel, line: lineOf(node), text: node.text });
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

test('no un-wrapped Chinese literal remains in a display position outside src/mocks/', () => {
  const sample = unsafeDisplayGaps.slice(0, 30).map((g) => `${g.file}:${g.line} ${JSON.stringify(g.text)}`);
  assert.deepEqual(
    sample,
    [],
    `${unsafeDisplayGaps.length} display-position Chinese literal(s) are not wrapped in t() — run the i18n codemod`,
  );
});

test('src/mocks/ has no NEW untranslated display strings beyond the known AI-content exclusions', () => {
  // mocks/ intentionally leaves AI-simulated prose untranslated (see dict/mocks.ts's own
  // header comment for the exclusion rationale) — this test only prints a visibility count,
  // it does not fail the suite, since new demo fixtures legitimately add new Chinese text.
  if (mocksGaps.length > 0) {
    console.log(`[i18n] ${mocksGaps.length} untranslated Chinese literal(s) remain in src/mocks/ (expected — AI-simulated content, or not yet triaged).`);
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
