import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../src/', import.meta.url));
const compiled = new Map();
function fixturesAt(instant) {
  const clock = Date.parse(instant), modules = new Map();
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : [clock])); }
    static now() { return clock; }
  }
  function load(filename) {
    if (!path.extname(filename)) filename += '.ts';
    if (modules.has(filename)) return modules.get(filename).exports;
    const module = { exports: {} };
    modules.set(filename, module);
    if (!compiled.has(filename)) compiled.set(filename, ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
    }).outputText);
    vm.runInNewContext(compiled.get(filename), {
      module, exports: module.exports, Date: Clock, Intl, TextEncoder,
      require(id) {
        if (id.includes('i18n/core')) return { t: value => value, getLocale: () => 'zh', localeTag: () => 'zh-CN' };
        if (id === '@/api/client') return { ApiError: class extends Error {} };
        return load(id.startsWith('@/') ? path.join(root, id.slice(2)) : path.resolve(path.dirname(filename), id));
      },
    });
    return module.exports;
  }
  return {
    fixtures: load(path.join(root, 'mocks/fixtures2.ts')),
    pool: load(path.join(root, 'mocks/data.ts')).TICKER_POOL,
    earnings: load(path.join(root, 'components/earnings/types.ts')),
    today: new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date(clock)),
  };
}

for (const instant of ['2026-09-12T12:00:00Z', '2026-09-01T02:00:00Z', '2026-01-01T02:00:00Z', '2028-03-01T02:00:00Z', '2026-07-05T12:00:00Z', '2026-01-31T12:00:00Z', '2028-01-31T12:00:00Z']) {
  test(`mock calendar has one report per real company and retains calendar coverage at ${instant}`, () => {
    const h = fixturesAt(instant), rows = h.fixtures.getEarningsUpcoming();
    const companies = h.pool.filter(row => !['SPY', 'QQQ'].includes(row.ticker));
    assert.equal(new Set(rows.map(row => row.ticker)).size, rows.length, 'ticker-only selection needs exactly one report per company');
    assert.deepEqual([...rows.map(row => row.ticker)].sort(), [...companies.map(row => row.ticker)].sort());
    const month = Date.parse(`${h.today.slice(0, 7)}-01T12:00:00Z`);
    const d = new Date(month);
    const first = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() - 1, 1)).toISOString().slice(0, 10);
    const last = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 2, 0)).toISOString().slice(0, 10);
    for (const row of rows) {
      assert.ok(row.date >= first && row.date <= last);
      assert.ok(![0, 6].includes(new Date(`${row.date}T12:00:00Z`).getUTCDay()), 'calendar keeps weekends empty');
      assert.equal(row.name, companies.find(company => company.ticker === row.ticker).name);
      if (row.date >= h.today) {
        assert.equal(row.epsActual, null);
        assert.equal(row.revActual, null);
      }
    }
    assert.equal(new Set(rows.map(row => row.date.slice(0, 7))).size, 3, 'previous/current/next month navigation retains real demo rows');
    assert.ok(rows.some(row => row.date < h.today && row.epsActual !== null));
    const defaultState = h.earnings.computeEarningsListState({ items: rows, mode: 'featured', personalTickers: new Set(), selectedDay: null, selectedTicker: null, visibleLimit: 24 });
    assert.ok(defaultState.listItems.length > 24, 'default featured list still exercises progressive expansion');
    assert.equal(defaultState.visibleItems.length, 24);
    assert.ok(defaultState.listItems.some(row => row.date < h.today && row.epsActual !== null), 'recent released EPS stays in the rolling list');
    const defaultRow = h.earnings.pickDefaultEarningsRow(defaultState.listItems);
    assert.ok(defaultRow.date >= h.today);
    for (const ticker of ['NVDA', 'MSFT', 'TSLA']) {
      const row = rows.find(item => item.ticker === ticker);
      const offset = (Date.parse(row.date) - Date.parse(h.today)) / 86_400_000;
      assert.ok(offset > 0 && offset <= 14);
      assert.equal(row.impactReady, true);
    }
    const nvda = rows.find(row => row.ticker === 'NVDA');
    const sameDay = rows.filter(row => row.date === nvda.date);
    assert.ok(sameDay.length >= 4, 'day chips retain the +N foldout scenario');
    const nonFeatured = sameDay.find(row => row.ticker === 'TSM');
    assert.equal(nonFeatured.publicFeatured, false);
    const selection = h.earnings.planTickerSelection({ items: rows, mode: 'featured', personalTickers: new Set(), ticker: 'TSM' });
    assert.equal(selection.mode, 'all');
    assert.deepEqual(h.fixtures.refreshEarningsUpcoming(), rows, 'refresh must not append an extra report identity');
    assert.deepEqual(h.fixtures.getEarningsUpcoming(), rows, 'reads remain deterministic in one demo session');
  });
}
