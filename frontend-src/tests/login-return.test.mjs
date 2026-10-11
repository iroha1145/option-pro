// 去登录的出发点：只收站内路径；每个登录入口都带上当前页；登录页读取时再校验一次。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { loginReturnState, safeReturnPath } from '../src/lib/loginReturn.ts';

const srcDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src');

test('only a single-slash same-site path is accepted as a login return target', () => {
  for (const ok of ['/', '/watchlist', '/breakouts?view=history#radar', '/stock/NVDA?range=1mo#options']) {
    assert.equal(safeReturnPath(ok), ok);
  }
  const rejected = [
    '//evil.com', '//evil.com/path?x=1', '/\\evil.com', '/\\/evil.com',
    'https://evil.com', 'http://evil.com/x', 'javascript:alert(1)', 'data:text/html,x',
    'evil.com', 'watchlist', '', ' /watchlist',
    null, undefined, 0, 42, true, {}, ['/watchlist'],
  ];
  for (const bad of rejected) assert.equal(safeReturnPath(bad), null, JSON.stringify(bad) ?? String(bad));
});

test('the login link state carries path, query and hash, and skips the login page and hostile paths', () => {
  assert.deepEqual(
    loginReturnState({ pathname: '/breakouts', search: '?view=history', hash: '#radar' }),
    { from: '/breakouts?view=history#radar' },
  );
  assert.deepEqual(loginReturnState({ pathname: '/', search: '', hash: '' }), { from: '/' });
  for (const pathname of ['/login', '/login/']) {
    assert.equal(loginReturnState({ pathname, search: '?x=1', hash: '' }), undefined, pathname);
  }
  assert.deepEqual(loginReturnState({ pathname: '/loginx', search: '', hash: '' }), { from: '/loginx' });
  // A page URL such as https://site//evil.com has pathname "//evil.com"; that must never become a target.
  assert.equal(loginReturnState({ pathname: '//evil.com', search: '', hash: '' }), undefined);
});

async function tsxFiles(dir) {
  const found = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) found.push(...await tsxFiles(full));
    else if (entry.name.endsWith('.tsx')) found.push(full);
  }
  return found;
}

test('every in-app entry to the login page passes the page it left, and Login validates what it reads', async () => {
  const entries = [];
  let fromPathInitializer = null;
  for (const file of await tsxFiles(srcDir)) {
    const ast = ts.createSourceFile(file, await readFile(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const visit = (node) => {
      // <Link to="/login" state={loginReturnState(...)}>
      if (ts.isJsxAttribute(node) && node.name.getText() === 'to' && node.initializer && ts.isStringLiteral(node.initializer) && node.initializer.text === '/login') {
        const state = node.parent.properties.find((p) => ts.isJsxAttribute(p) && p.name.getText() === 'state');
        entries.push({ where: `${path.relative(srcDir, file)} <Link>`, ok: Boolean(state?.initializer?.getText().includes('loginReturnState(')) });
      }
      // navigate('/login', { state: loginReturnState(...) })
      if (ts.isCallExpression(node) && node.expression.getText() === 'navigate' && ts.isStringLiteralLike(node.arguments[0]) && node.arguments[0].text === '/login') {
        entries.push({ where: `${path.relative(srcDir, file)} navigate()`, ok: Boolean(node.arguments[1]?.getText().includes('loginReturnState(')) });
      }
      if (ts.isVariableDeclaration(node) && node.name.getText() === 'fromPath' && path.basename(file) === 'Login.tsx') {
        fromPathInitializer = node.initializer?.getText() ?? '';
      }
      ts.forEachChild(node, visit);
    };
    visit(ast);
  }
  assert.ok(entries.length >= 9, `expected the known login entries, found ${entries.length}`);
  assert.deepEqual(entries.filter((entry) => !entry.ok).map((entry) => entry.where), []);
  assert.match(fromPathInitializer ?? '', /^safeReturnPath\(/);
});
