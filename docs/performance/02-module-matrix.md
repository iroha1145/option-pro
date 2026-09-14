# 全站模块矩阵

以仓库实际功能为准（`frontend-src/src/App.tsx` + `backend/app/main.py`）。基线 SHA `31e8955d`。性能基线列在测量完成后回填；本表先固定功能与数据链路。

覆盖评级：有 = 专测充分；弱 = 仅契约/间接；无 = 缺专项。

## 页面

| 模块 | 路由 | 首屏数据链路 | 关键交互 | 前端测试 | pytest | 性能基线 |
|------|------|--------------|----------|----------|--------|----------|
| 首页 | `/` | indices/status/regime/signals/strength/breakouts/earnings/watchlist/cta/calendar/data-status | 指数跳转、区块重试、日历切换、实时报价订阅 | 有（契约）视觉弱 | 间接 | n=20 p75 1638（`browser-pages-n20.json`） |
| 自选 | `/watchlist` | market status、watchlist、signals、strength、account watchlist | 排序、增删、强制刷新、渐进列表、表格/卡片 | 有 | 有 | n=20 p75 1795 |
| 选股 | `/screener` | strength scan/market/profiles；扫描时 batch catalysts | 筛选、扫描、分页 20、行展开 | 有 | 有 | n=20 p75 1689 |
| 雷达 | `/breakouts` | status/current/events | 筛选、立即扫描、事件详情、历史分页 100 | 弱～有 | 有 | n=20 p75 1626 |
| 板块 | `/sectors` | sectors、strength/sectors、iv-ranking | 热力/列表、周期、IV | 弱 | 弱 | n=20 p75 1571 |
| 财报 | `/earnings` | upcoming；Owner refresh；AI impact | 周/月历、分页 24、分析任务 | 有 | 有 | n=20 p75 1606 |
| 新闻 | `/catalysts` | status、hotspots/status、feed×2、hotspots、focus latest/previous、Owner analysis-progress | 筛选/搜索/分类、游标分页 12、详情抽屉、刷新 | 有 | 有 | **最终 n=20 冷 1698 / 热 838**；交错未优化 2392 / 1093 |
| 大盘 | `/market` | 同首页核心 + macro conditions/history | 指数聚焦、宏观刷新 | 有 | 有 | n=20 p75 1715 |
| CTA | `/cta` | market/cta、strength/market | instrument 切换 | 弱 | 间接 | n=20 p75 2167 |
| 个股 | `/stock/:ticker` | detail+technical 并行；预取 chart/signals；期权/新闻/绘图 | 周期、绘图、期权链、手动拉取 | 有 | 有 | `/stock/NVDA` n=20 p75 2148（骨架不算就绪） |
| 登录 | `/login` | access/status；login/register | Owner/客户登录 | 弱～有 | 有 | Owner 已登录 n=20 p75 1239；**访客表单未测** |
| 404 | `*` | 无 | 返回首页 | 无 | 无 | n=20 p75 1154 |

## 壳与后台

| 模块 | 入口 | 数据链路 | 测试 |
|------|------|----------|------|
| Layout / Navbar / IndexTape / MobileDock / CommandPalette | 全局 | market status/indices、stocks/search、QuoteConnection SSE | 分散 |
| ManagePanel | `/catalysts` Owner | catalysts/refresh、worker status/actions、runtime-settings | 弱 |
| Runtime settings | 财报页 + ManagePanel | GET/PUT/rollback runtime-settings | 有（后端） |
| 账号自选 / 绘图 | Watchlist、StockDetail | `/api/account/*` | 有 |
| Worker 13 项任务 | 进程 `app.worker` | 见 README 健康检查清单 | 有（后端） |
| 诊断缓存 | 无 UI | `GET /api/diagnostics/cache` | 弱 |

## 新闻页请求扇出（默认 tab=feed）

| 调用方 | 接口 | 间隔 | 后端代价（代码） |
|--------|------|------|------------------|
| StatusHero | `GET /api/catalysts/status` | 45s | ETL state + 24h COUNT，不扫 revision 全表 |
| StatusHero | `GET /api/catalysts/hotspots/status` | 45s | LIMIT 1 + 内嵌再跑一遍 status |
| StatusHero | `newsToday` → `GET /api/catalysts/feed?window_hours=24&limit=50` | 120s | **整窗物化** |
| FeedPanel | `GET /api/catalysts/feed?limit=12&window_hours=72` | 120s fresh | **整窗物化后再切片** |
| HotspotsStrip | hotspots + hotspots/status | 120s / 45s | 有界 LIMIT |
| FocusCycleCard | market-focus-cycles/latest（及 previous） | 挂载 | 有界 |
| AnalysisProgressCard | analysis-progress | Owner 5s/30s | Owner only |
| 页头刷新 | `clearCatalystReadCache` + refreshToken | 手动 | 穿透客户端读缓存 |

Calendar / sources 不在首屏，切 tab 才拉。

## API 前缀（网关默认 `private, no-store`，例外见表）

公开读：stocks、options、earnings、sectors、market、quotes、macro、signals、catalysts、strength、breakouts。
Owner：ai、worker、runtime-settings、diagnostics、settings。
自管：access、account。
健康：`/health`、`/ready`。

`GET /api/market/indices`：`max-age=30, swr=120`。`GET /api/market/cta`：`max-age=60, swr=300`。

## Worker 写入

| 任务 | 主要写入 |
|------|----------|
| breakout / breakout_refresh | `optix.db` |
| catalyst_sync | `catalyst-cache.db` |
| focus | `ai-jobs.db` + catalyst |
| ai_jobs | `ai-jobs.db` |
| maintenance / retention | `backups/` 与 prune |
| stock_directory | `stock-symbol-directory-v1.json` |
| public_home | `public-home-snapshot-v1.json` |
| earnings_analysis | 入队 ai-jobs |
| macro_conditions | `macro-conditions.db` |
| focus_refresh | `watchlist-snapshot-v1.json` |
| strength_refresh | `strength-snapshot-v1.json` |

## 已有性能相关实现（不重复发明）

- `queryRegistry` / `sharedRead`：公开读合并与短 TTL（未覆盖 catalysts feed）
- `useProgressiveList`：自选渐进挂载，不是数据截断
- Logo 磁盘缓存、报价 delta fanout、page-enter CSS、路由 lazy、单层 Gateway、GZip + `/assets` immutable
- 新闻 feed 已有服务端游标分页，但 SQL 未下推 LIMIT
