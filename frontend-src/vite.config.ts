import fs from "fs"
import path from "path"
import react from "@vitejs/plugin-react"
import ts from "typescript"
import { defineConfig, type Plugin } from "vite"

const MOCK_MODULE = /[\\/]src[\\/]mocks[\\/][^\\/]+\.ts$/

/** Exported *value* names of a TypeScript module, via the compiler, not a regex. */
function exportedValueNames(source: string, fileName: string): string[] {
  const parsed = ts.createSourceFile(
    fileName,
    source,
    ts.ScriptTarget.ESNext,
    true,
    ts.ScriptKind.TS,
  )
  const names = new Set<string>()
  const isExported = (node: ts.Node) =>
    ts.canHaveModifiers(node)
    && (ts.getModifiers(node) ?? []).some(
      (modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword,
    )

  for (const statement of parsed.statements) {
    if (ts.isVariableStatement(statement) && isExported(statement)) {
      for (const declaration of statement.declarationList.declarations) {
        if (ts.isIdentifier(declaration.name)) names.add(declaration.name.text)
      }
    } else if (
      (ts.isFunctionDeclaration(statement) || ts.isClassDeclaration(statement))
      && isExported(statement)
      && statement.name
    ) {
      names.add(statement.name.text)
    } else if (ts.isEnumDeclaration(statement) && isExported(statement)) {
      names.add(statement.name.text)
    } else if (
      ts.isExportDeclaration(statement)
      && !statement.isTypeOnly
      && statement.exportClause
      && ts.isNamedExports(statement.exportClause)
    ) {
      for (const element of statement.exportClause.elements) {
        if (!element.isTypeOnly) names.add(element.name.text)
      }
    } else if (ts.isExportDeclaration(statement) && !statement.exportClause) {
      // `export * from './x'` would need the target module's export list too.
      // Nothing under src/mocks uses it; fail loudly rather than under-stub.
      throw new Error(
        `optix-strip-mocks: ${fileName} uses \`export *\`, which this plugin does not handle`,
      )
    }
  }
  return [...names]
}

/**
 * Replace src/mocks/* with same-shaped stubs in the live build.
 *
 * The fixtures are dead in live mode -- every call sits behind an `isMock`
 * guard -- but they were still *linked*: `import * as fx from '@/mocks/fixtures'`
 * is a static reference, so Rollup cannot drop the module, and 133KB of source
 * fixtures shipped to every visitor. They landed in the entry chunk and in a
 * shared chunk on the watchlist critical path (~19KB gzip of the 45KB
 * TickerLogo chunk, which is named after a 14-line component and is mostly mock
 * company data).
 *
 * Stubbing rather than rewriting call sites: there are 89 fixture references
 * across 14 files, and threading a lazy import through all of them is a far
 * larger and riskier change than replacing one directory's module bodies.
 *
 * Types need no handling here. `import type` is erased by esbuild before Vite
 * resolves anything, and `tsc -b` type-checks against the real sources.
 *
 * The failure mode is deliberately build-time: a missed export makes Rollup fail
 * with "does not provide an export named X" rather than shipping something that
 * breaks in a visitor's browser.
 */
function stripMocksFromLiveBuild(live: boolean): Plugin {
  return {
    name: "optix-strip-mocks-from-live-build",
    enforce: "pre",
    apply: "build",
    load(id) {
      if (!live) return null
      const file = id.split("?")[0]
      if (!MOCK_MODULE.test(file)) return null
      const names = exportedValueNames(fs.readFileSync(file, "utf8"), file)
      // `undefined`, not a thrower. A few of these are consts read inside
      // branches live mode never renders, and a getter that throws on access
      // would turn an unreachable branch into a crash. A *call* still fails
      // loudly, which is what a real live-mode fixture call deserves.
      return `${names
        .map((name) => `export const ${name} = undefined;`)
        .join("\n")}\nexport {};\n`
    },
  }
}

const EPS_CHART_MODULE = path.resolve(__dirname, 'src/components/earnings/EpsHatchChart.tsx');
const EPS_CHART_URL = 'virtual:eps-chart-url';
const RESOLVED_EPS_CHART_URL = `\0${EPS_CHART_URL}`;
const CHART_MODULES = new Set([
  EPS_CHART_MODULE,
  path.resolve(__dirname, 'src/components/charts/ReactECharts.tsx'),
  path.resolve(__dirname, 'src/lib/chart.ts'),
  path.resolve(__dirname, 'src/lib/chartFonts.ts'),
]);

/** Keep all not-yet-loaded chart dependencies under the same retryable URL. */
function chartManualChunk(id: string): string | undefined {
  const file = id.split('?')[0];
  if (CHART_MODULES.has(file) || /[/\\]node_modules[/\\](echarts|zrender)[/\\]/.test(file)) {
    return 'eps-chart';
  }
}

/**
 * Load the application shell in one request after prepareI18n(), rather than
 * spreading its always-needed code over dozens of small shared chunks. Keep
 * the entry's static dependencies separate: evaluating module-level t() before
 * the selected dictionary is installed would freeze navigation in Chinese.
 */
function applicationManualChunks() {
  let shell: Set<string> | undefined;
  return (id: string, { getModuleInfo }: {
    getModuleInfo: (moduleId: string) => { importedIds: readonly string[] } | null;
  }): string | undefined => {
    const chart = chartManualChunk(id);
    if (chart) return chart;
    if (!shell) {
      const closure = (start: string) => {
        const seen = new Set<string>();
        const visit = (moduleId: string) => {
          if (seen.has(moduleId)) return;
          seen.add(moduleId);
          getModuleInfo(moduleId)?.importedIds.forEach(visit);
        };
        visit(start);
        return seen;
      };
      const entry = closure(path.resolve(__dirname, 'src/main.tsx'));
      shell = new Set([...closure(path.resolve(__dirname, 'src/App.tsx'))]
        .filter((moduleId) => !entry.has(moduleId)));
    }
    if (shell.has(id) && !/\.(?:css|scss|sass|less|styl)(?:\?|$)/.test(id)) return 'app-shell';
  };
}

function verifyApplicationShell(): Plugin {
  return {
    name: 'optix-verify-application-shell',
    apply: 'build',
    generateBundle(_options, bundle) {
      const chunks = Object.values(bundle).filter((asset) => asset.type === 'chunk');
      const shell = chunks.find((chunk) => Object.keys(chunk.modules)
        .some((id) => id.replaceAll('\\', '/').endsWith('/src/App.tsx')));
      if (!shell || shell.name !== 'app-shell') this.error('App must be emitted in the deferred application shell.');
      const forbidden = Object.keys(shell.modules).filter((id) => {
        const file = id.replaceAll('\\', '/');
        return /\/src\/(main\.tsx|i18n\/boot\.ts|i18n\/dict\/runtime-(?:en|ja)\.ts)/.test(file)
          // NotFound is deliberately a small static child of App; the real
          // route pages must remain separate dynamic imports.
          || (/\/src\/pages\//.test(file) && !file.endsWith('/NotFound.tsx'))
          || chartManualChunk(id) !== undefined;
      });
      if (forbidden.length) this.error(`Application shell contains eager or deferred modules: ${forbidden.join(', ')}`);
      const seen = new Set<string>();
      const visit = (name: string) => {
        if (seen.has(name)) return;
        seen.add(name);
        const chunk = bundle[name];
        if (chunk?.type === 'chunk') chunk.imports.forEach(visit);
      };
      // The chart is an emitted entry for URL generation, not the document's
      // startup entry. Only the main module defines eager page evaluation.
      chunks.filter((chunk) => Object.keys(chunk.modules).some((id) =>
        id.replaceAll('\\', '/').endsWith('/src/main.tsx'))).forEach((chunk) => visit(chunk.fileName));
      if (seen.has(shell.fileName)) this.error('Application shell executes before prepareI18n has completed.');
    },
  };
}

function epsChartChunk(): Plugin {
  let build = false;
  let referenceId = '';
  return {
    name: 'optix-eps-chart-chunk',
    configResolved(config) { build = config.command === 'build'; },
    buildStart() {
      if (build) referenceId = this.emitFile({
        type: 'chunk', id: EPS_CHART_MODULE, name: 'eps-chart', preserveSignature: 'allow-extension',
      });
    },
    resolveId(id) { if (id === EPS_CHART_URL) return RESOLVED_EPS_CHART_URL; },
    load(id) {
      if (id !== RESOLVED_EPS_CHART_URL) return;
      // Rollup owns the hashed file name and relative URL. Never inspect a
      // minified function's source or resolve an uncompiled TS specifier.
      return build
        ? `export default import.meta.ROLLUP_FILE_URL_${referenceId};`
        : `export default '/src/components/earnings/EpsHatchChart.tsx';`;
    },
    generateBundle(_options, bundle) {
      const fileName = this.getFileName(referenceId);
      const chart = bundle[fileName];
      if (!chart || chart.type !== 'chunk' || !(EPS_CHART_MODULE in chart.modules)) {
        this.error('EPS recovery must address the chart implementation, not a separate entry facade.');
      }
      const closure = (starts: string[]): Set<string> => {
        const seen = new Set<string>();
        const visit = (name: string) => {
          if (seen.has(name)) return;
          seen.add(name);
          const chunk = bundle[name];
          if (chunk?.type === 'chunk') chunk.imports.forEach(visit);
        };
        starts.forEach(visit);
        return seen;
      };
      const chunks = Object.values(bundle).filter((asset) => asset.type === 'chunk');
      const findModule = (suffix: string) => chunks.find((chunk) =>
        Object.keys(chunk.modules).some((id) => id.replaceAll('\\', '/').endsWith(suffix)))?.fileName;
      const app = findModule('/src/App.tsx');
      const earnings = findModule('/src/pages/Earnings.tsx');
      if (!app || !earnings) this.error('Cannot verify EPS recovery dependencies without App and Earnings chunks.');
      const loaded = closure([...chunks.filter((chunk) => chunk.isEntry && chunk.fileName !== fileName)
        .map((chunk) => chunk.fileName), app, earnings]);
      if (loaded.has(fileName)) this.error('EPS chart must not be a static dependency of the app or Earnings page.');
      const missing = [...closure(chart.imports)].filter((name) => !loaded.has(name));
      if (missing.length) this.error(`EPS chart has dependencies outside the loaded page: ${missing.join(', ')}`);
      if (Object.keys(chart.modules).some((id) => /[/\\]node_modules[/\\](react|react-dom|react-router)[/\\]/.test(id))) {
        this.error('EPS recovery must reuse the loaded React and router instances.');
      }
    },
  };
}

// https://vite.dev/config/
export default defineConfig(() => {
  const live = process.env.VITE_API_MODE === "live"
  return {
    // 根路径配信专用。相对 base 在 /stock/NVDA 这类二级深链上会把资产解析成
    // /stock/assets/*（网关对带扩展名路径如实 404）——硬刷新详情页直接白屏。
    // 详情页改为全屏整页后深链/刷新是常规路径，与 JP 站同口径改为绝对根。
    base: '/',
    plugins: [react(), stripMocksFromLiveBuild(live), epsChartChunk(), verifyApplicationShell()],
    build: {
      rollupOptions: {
        output: { manualChunks: applicationManualChunks(), onlyExplicitManualChunks: true },
      },
    },
    server: {
      port: 3000,
      proxy: {
        // 默认只连接本机后端；生产调试必须显式设置 OPTIX_API_PROXY。
        // headers.origin 改写为目标源:后端 require_same_origin_* 校验 Origin==Host,
        // 否则 dev 下所有写操作(batch/登录/触发)都会被如实拒绝
        "/api": {
          target: process.env.OPTIX_API_PROXY || "http://127.0.0.1:2000",
          changeOrigin: true,
          secure: true,
          headers: {
            origin: process.env.OPTIX_API_PROXY || "http://127.0.0.1:2000",
          },
        },
      },
    },
    // 同一套反代也给 preview 用:构建产物 + live API 是唯一能真正跑到
    // stripMocksFromLiveBuild 产出的 stub 的组合(该插件只在 build 阶段生效,
    // dev server 用的是真 fixture),不这样验就等于把它直接推到生产。
    preview: {
      port: 4173,
      proxy: {
        "/api": {
          target: process.env.OPTIX_API_PROXY || "http://127.0.0.1:2000",
          changeOrigin: true,
          secure: true,
          headers: {
            origin: process.env.OPTIX_API_PROXY || "http://127.0.0.1:2000",
          },
        },
      },
    },
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
  }
});
