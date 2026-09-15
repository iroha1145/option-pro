/**
 * 最终生产包的静态 JS 导入图。gzip9 口径，不含 CSS / 数据 / 运行时预取。
 * 用来避免把「入口 + App」写成完整首次下载量。
 */
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';

const execFileAsync = promisify(execFile);
const IMPORT_RE = /(?:from|import)\s*["'](\.\/[^"']+\.js)["']/g;

export async function gzip9Size(filePath) {
  const { stdout } = await execFileAsync('gzip', ['-9', '-c', filePath], {
    encoding: 'buffer',
    maxBuffer: 32 * 1024 * 1024,
  });
  return stdout.length;
}

export function parseStaticJsImports(source) {
  const specs = [];
  for (const match of source.matchAll(IMPORT_RE)) specs.push(match[1]);
  return specs;
}

export async function walkStaticJsGraph(frontendDir, entryRel) {
  const seen = new Map();
  const queue = [entryRel.replace(/^\.\//, '')];
  while (queue.length) {
    const rel = queue.shift();
    if (seen.has(rel)) continue;
    const abs = path.join(frontendDir, rel);
    const source = await readFile(abs, 'utf8');
    const raw = (await stat(abs)).size;
    seen.set(rel, { path: `frontend/${rel}`, raw, gzip9: await gzip9Size(abs) });
    const dir = path.posix.dirname(rel);
    for (const spec of parseStaticJsImports(source)) {
      const next = path.posix.normalize(`${dir}/${spec}`);
      if (!seen.has(next)) queue.push(next);
    }
  }
  const files = [...seen.values()].sort((a, b) => a.path.localeCompare(b.path));
  return {
    entry: `frontend/${entryRel.replace(/^\.\//, '')}`,
    script_n: files.length,
    gzip9: files.reduce((sum, file) => sum + file.gzip9, 0),
    raw: files.reduce((sum, file) => sum + file.raw, 0),
    files,
  };
}

export function subtractGraph(full, sharedPaths) {
  const shared = new Set(sharedPaths);
  const files = full.files.filter((file) => !shared.has(file.path));
  return {
    script_n: files.length,
    gzip9: files.reduce((sum, file) => sum + file.gzip9, 0),
    raw: files.reduce((sum, file) => sum + file.raw, 0),
    files,
  };
}
