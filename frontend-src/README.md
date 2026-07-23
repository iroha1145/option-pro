# Optix Pro · 前端重构（纸面终端 Paper Terminal）

美股投资研究工作台 · React 19 + TypeScript + Vite 7 + Tailwind CSS v3.4 + Framer Motion + ECharts（按需）。
设计单一事实来源：`/mnt/agents/output/design/design.md`（全局）与各页面 md。

## 开发

```bash
npm run dev     # 开发（端口 3000）
npm run build   # 类型检查 + 产物构建
```

## API 模式（VITE_API_MODE）

- 默认 `mock`：本地 fixtures（`src/mocks/`，确定性种子），随机延迟 250–700ms。
- `VITE_API_MODE=live npm run dev`：走同源 `/api` + `credentials:'include'`（HttpOnly Cookie 会话）。
- 接口形状与后端契约 1:1（`src/api/types.ts`）；错误形状 `{ code, message }`，503 → 「快照不可用」空态。

## 目录约定

```
src/api/         client.ts（mock/live 切换）+ modules/（12 域）+ types.ts
src/mocks/       确定性种子 fixtures + session（写操作内存落盘）
src/components/  Navbar / Footer / Layout（<Outlet/> 嵌套路由）/ CommandPalette / Drawer / Toast / MobileDock
src/components/shared/   StatCard TickerLogo ChangeBadge StrengthBar SignalChip SessionLED
                         Segmented HatchLegend SourceNote EmptyState Skeleton DataTable PageHeader
src/components/charts/   ReactECharts 包装 + Sparkline（含点阵面积工艺）
src/components/icons.tsx 手绘细线图标（24 viewBox · 1.6px stroke · currentColor）
src/lib/chart.ts ECharts 按需注册 + 全站统一工艺（发丝网格/毛玻璃 tooltip/斜纹/点阵/热力色阶）
src/hooks/       usePolling（页面不可见暂停）· useAccess（visitor/owner）· useCountUp · useNow
public/          手绘 SVG 资产（logo / empty-* / login-motif / texture-dots）
```

- 页面代理：导航高亮/间距由 `Layout` 统一负责，页面内不要重复 sticky 头部偏移。
- 动效/色值/字号只用 design.md 的具名 token（tailwind.config 已全量落地）。
