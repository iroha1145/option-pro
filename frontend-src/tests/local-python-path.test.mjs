import test from 'node:test';
import assert from 'node:assert/strict';
import { chmodSync, mkdirSync, writeFileSync } from 'node:fs';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import { resolveVenvPython } from '../visual-tests/support/localPython.mjs';

function seedVenv(projectDir) {
  const python = join(projectDir, '.venv', 'bin', 'python');
  mkdirSync(join(projectDir, '.venv', 'bin'), { recursive: true });
  writeFileSync(python, '#!/bin/sh\nexit 0\n', { mode: 0o755 });
  chmodSync(python, 0o755);
  return python;
}

function configUrl(projectDir) {
  return pathToFileURL(join(projectDir, 'frontend-src', 'playwright.customer-charts.config.mjs')).href;
}

for (const name of ['option-pro', 'option pro', '中文项目']) {
  test(`resolveVenvPython decodes ${JSON.stringify(name)} and the interpreter can start`, () => {
    const root = mkdtempSync(join(tmpdir(), 'optix-python-path-'));
    const projectDir = join(root, name);
    mkdirSync(join(projectDir, 'frontend-src'), { recursive: true });
    const expected = seedVenv(projectDir);
    const fromUrl = configUrl(projectDir);

    const encodedPathname = new URL('../.venv/bin/python', fromUrl).pathname;
    const resolved = resolveVenvPython(fromUrl, {});

    assert.equal(resolved, expected);
    if (name !== 'option-pro') {
      assert.match(encodedPathname, /%/);
      assert.notEqual(encodedPathname, expected);
      const encoded = spawnSync(encodedPathname, { encoding: 'utf8' });
      assert.notEqual(encoded.status, 0);
    }

    const decoded = spawnSync(resolved, { encoding: 'utf8' });
    assert.equal(decoded.status, 0, decoded.stderr || decoded.error?.message);
  });
}

test('OPTIX_PYTHON_EXECUTABLE wins over a local venv', () => {
  const root = mkdtempSync(join(tmpdir(), 'optix-python-override-'));
  mkdirSync(join(root, 'frontend-src'), { recursive: true });
  seedVenv(root);
  assert.equal(
    resolveVenvPython(configUrl(root), { OPTIX_PYTHON_EXECUTABLE: '/custom/python' }),
    '/custom/python',
  );
});
