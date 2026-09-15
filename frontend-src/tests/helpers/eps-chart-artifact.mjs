import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { parseStaticJsImports } from '../../../scripts/perf/lib/round6_bundle_graph.mjs';

/** Shared by the committed-artifact gate and the fresh-build behavior test. */
export function assertEpsChartArtifact(artifactDir) {
  const assets = path.join(artifactDir, 'assets');
  const files = fs.readdirSync(assets).filter((name) => name.endsWith('.js'));
  const code = new Map(files.map((name) => [name, fs.readFileSync(path.join(assets, name), 'utf8')]));
  const imports = new Map([...code].map(([name, text]) => [name,
    parseStaticJsImports(text).map((dep) => path.basename(dep)),
  ]));
  const closure = (starts) => {
    const seen = new Set();
    const visit = (name) => {
      if (seen.has(name)) return;
      seen.add(name);
      for (const dependency of imports.get(name) ?? []) visit(dependency);
    };
    starts.forEach(visit);
    return seen;
  };
  const named = (prefix) => {
    const matches = files.filter((name) => name.startsWith(prefix));
    assert.equal(matches.length, 1, prefix);
    return matches[0];
  };
  const chart = named('eps-chart-');
  const earnings = named('Earnings-');
  const app = files.find((name) => name.startsWith('App-')) ?? named('app-shell-');
  const html = fs.readFileSync(path.join(artifactDir, 'index.html'), 'utf8');
  const moduleScript = [...html.matchAll(/<script\b[^>]*>/g)]
    .map(([tag]) => tag).find((tag) => /\btype="module"/.test(tag));
  const entry = path.basename(moduleScript?.match(/\bsrc="([^"]+\.js)"/)?.[1] ?? '');
  assert.ok(code.has(entry), 'identify the real entry through index.html');
  assert.ok(code.get(entry).includes(app), 'application chunk must be referenced by the entry');
  assert.ok(!closure([entry]).has(chart), 'chart must not enter the HTML entry dependency graph');
  const loaded = closure([earnings, app]);
  assert.ok(!loaded.has(chart), 'Earnings must defer the chart bundle');
  assert.ok(!closure([app, named('Home-')]).has(chart), 'Home must not inherit ECharts');
  assert.ok(!closure([app, named('Screener-')]).has(chart), 'shared legend must not pull ECharts into Screener');
  for (const dependency of closure(imports.get(chart))) {
    assert.ok(loaded.has(dependency), `retry still depends on unloaded ${dependency}`);
  }
  assert.ok(code.get(chart).includes('data-eps-chart'), 'URL must target the component implementation');
  const generatedUrl = new RegExp(`new URL\\(["']${chart.replaceAll('.', '\\.') }["'],import\\.meta\\.url\\)\\.href`);
  assert.match(code.get(earnings), generatedUrl, 'loader must use Rollup-generated URL of the real module');
  assert.match(code.get(earnings), /searchParams\.set\(["']recover["']/, 'retry must change the module URL');
  assert.doesNotMatch(code.get(earnings), /Unknown variable dynamic import/, 'retry URL must use native import');
  assert.doesNotMatch(code.get(earnings), /import\.meta\.resolve|String\(\(\)=>/);
}
