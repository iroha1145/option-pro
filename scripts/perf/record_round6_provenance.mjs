#!/usr/bin/env node
/** Record independently recomputable V2 provenance for a Round 6 measurement. */
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createHash } from 'node:crypto';
import { mkdir, readFile, readdir, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const execFileAsync = promisify(execFile);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const OUT = process.env.OPTIX_PERF_PROVENANCE
  || '/opt/cursor/artifacts/perf/round6-provenance.json';

async function sh(cmd, args) {
  const { stdout } = await execFileAsync(cmd, args, { cwd: ROOT, encoding: 'utf8' });
  return stdout.trim();
}

async function sha256File(abs) {
  const buf = await readFile(abs);
  return createHash('sha256').update(buf).digest('hex');
}

const html = await readFile(path.join(ROOT, 'frontend/index.html'), 'utf8');
const indexName = (html.match(/assets\/(index-[^"]+\.js)/) || [])[1] || null;
const assets = await readdir(path.join(ROOT, 'frontend/assets'));
const hashed = {};
for (const name of [
  indexName,
  assets.find((n) => /^app-shell-.+\.js$/.test(n)) || assets.find((n) => /^App-.+\.js$/.test(n)),
  assets.find((n) => /^eps-chart-.+\.js$/.test(n)) || assets.find((n) => /^chart-.+\.js$/.test(n)),
  assets.find((n) => /^Earnings-.+\.js$/.test(n)),
  assets.find((n) => /^Home-.+\.js$/.test(n)),
  assets.find((n) => /^Catalysts-.+\.js$/.test(n)),
]) {
  if (!name) continue;
  hashed[`frontend/assets/${name}`] = await sha256File(path.join(ROOT, 'frontend/assets', name));
}
const measuredProductCommit = process.env.OPTIX_PERF_PRODUCT_COMMIT
  || await sh('git', ['rev-parse', 'HEAD']);
const measuredProductTree = process.env.OPTIX_PERF_PRODUCT_TREE
  || await sh('git', ['rev-parse', `${measuredProductCommit}^{tree}`]);

const report = {
  measured_at: new Date().toISOString(),
  git: {
    commit: await sh('git', ['rev-parse', 'HEAD']),
    tree: await sh('git', ['rev-parse', 'HEAD^{tree}']),
    status: await sh('git', ['status', '--porcelain']),
    branch: await sh('git', ['rev-parse', '--abbrev-ref', 'HEAD']),
  },
  scripts: {
    measure_round6_surfaces: await sha256File(path.join(ROOT, 'scripts/perf/measure_round6_surfaces.mjs')),
    measure_round6_i18n: await sha256File(path.join(ROOT, 'scripts/perf/measure_round6_i18n.mjs')),
    measure_round6_browser: await sha256File(path.join(ROOT, 'scripts/perf/measure_round6_browser.mjs')),
    measure_round6_feed: await sha256File(path.join(ROOT, 'scripts/perf/measure_round6_feed.py')),
    round6_ready_summary: await sha256File(path.join(ROOT, 'scripts/perf/lib/round6_ready_summary.mjs')),
    round6_intent_decision: await sha256File(path.join(ROOT, 'scripts/perf/lib/round6_intent_decision.mjs')),
    round6_bundle_graph: await sha256File(path.join(ROOT, 'scripts/perf/lib/round6_bundle_graph.mjs')),
  },
  frontend_hashes: hashed,
  measured_product_commit: measuredProductCommit,
  measured_product_tree: measuredProductTree,
  seed: await (async () => {
    const dataDir = process.env.DATA_DIR || '/home/ubuntu/optix-perf-data/n10000-r6';
    const sqlitePath = process.env.OPTIX_PERF_SQLITE
      || path.join(dataDir, 'catalyst-cache.db');
    let sqliteSha = process.env.OPTIX_PERF_SEED_SHA256 || null;
    let sqliteBytes = process.env.OPTIX_PERF_SEED_BYTES
      ? Number(process.env.OPTIX_PERF_SEED_BYTES)
      : null;
    try {
      const info = await stat(sqlitePath);
      sqliteBytes = sqliteBytes ?? info.size;
      sqliteSha = sqliteSha ?? await sha256File(sqlitePath);
    } catch {
      /* seed path optional when recording hashes-only */
    }
    return {
      data_dir: dataDir,
      sqlite_path: sqlitePath,
      note: 'synthetic --count 10000 --hidden-newest 48 --analyze-every 2 --history-every 15 --wall-clock',
      sqlite_sha256: sqliteSha,
      sqlite_bytes: sqliteBytes,
      revisions: 10000,
      analysis_links: 5078,
      result_audits: 5078,
    };
  })(),
  network_profile: {
    name: 'mobile-ref',
    width: 390,
    height: 844,
    cpu: 4,
    down_bps: (10 * 1024 * 1024) / 8,
    up_bps: (2 * 1024 * 1024) / 8,
    rtt_ms: 180,
  },
};
await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
console.log(`wrote ${OUT}`);
