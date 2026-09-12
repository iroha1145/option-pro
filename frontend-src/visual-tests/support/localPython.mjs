import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

/**
 * Resolve the repo `.venv` interpreter from a Playwright config's `import.meta.url`.
 * `URL.pathname` leaves spaces / CJK percent-encoded; `fileURLToPath` decodes them.
 */
export function resolveVenvPython(fromImportMetaUrl, env = process.env) {
  if (env.OPTIX_PYTHON_EXECUTABLE) return env.OPTIX_PYTHON_EXECUTABLE;
  const localPython = fileURLToPath(new URL('../.venv/bin/python', fromImportMetaUrl));
  return existsSync(localPython) ? localPython : 'python3';
}

/** POSIX single-quote so Playwright's `shell: true` does not expand `$` / backticks. */
export function posixShellQuote(value) {
  return `'${String(value).replace(/'/g, `'\\''`)}'`;
}

export function quotedPythonCommand(pythonExecutable, scriptAndArgs) {
  return `${posixShellQuote(pythonExecutable)} ${scriptAndArgs}`;
}
