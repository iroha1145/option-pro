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

// https://vite.dev/config/
export default defineConfig(() => {
  const live = process.env.VITE_API_MODE === "live"
  return {
    base: './',
    plugins: [react(), stripMocksFromLiveBuild(live)],
    server: {
      port: 3000,
      proxy: {
        // 本地 live 调试:/api 反代到生产(只读验证用)
        // headers.origin 改写为目标源:后端 require_same_origin_* 校验 Origin==Host,
        // 否则 dev 下所有写操作(batch/登录/触发)都会被如实拒绝
        "/api": {
          target: process.env.OPTIX_API_PROXY || "https://option.openweb-ui.xyz",
          changeOrigin: true,
          secure: true,
          headers: {
            origin: process.env.OPTIX_API_PROXY || "https://option.openweb-ui.xyz",
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
          target: process.env.OPTIX_API_PROXY || "https://option.openweb-ui.xyz",
          changeOrigin: true,
          secure: true,
          headers: {
            origin: process.env.OPTIX_API_PROXY || "https://option.openweb-ui.xyz",
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
