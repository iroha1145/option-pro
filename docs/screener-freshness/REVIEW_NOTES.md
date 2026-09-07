# 复审说明

## 方案

采用 **方案 B**：保留按参数分文件的快照，补齐新鲜度裁决、管理员任务链和有上限的近期变体刷新。扫描器本身已先评完整宇宙再过滤，因此没有重写成共享全池基础快照。

## 最危险的共享模块

- `frontend-src/src/api/marketRead.ts`：个股抽屉、选股读回共用。force 不再合流旧 GET，并加 `cache:'reload'`。429 退避与身份世代隔离保留；精确失效只用 `resetMarketReadPaths`。
- `frontend-src/src/lib/liveQuotes.ts` / `LiveQuote.tsx`：全站报价标签与比价。备用价不再显示「定时更新」。
- `backend/app/worker/tasks.py` `StrengthRefreshTask`：定时路径现在额外刷最多 4 个变体；变体异常被吞并计数，默认定时结果仍返回。

## 新鲜度政策

见 `DESIGN_DECISIONS.md`。审查时请核对：未知时间不会被写成当前时间；今天写盘 + 旧日线必须 stale/historical；休市合法不变不是失败。

## 任务去重与超时

- 前端同参数恢复 `sessionStorage` 中的 `requestId`。
- 后端 worker action 仍有既有冷却/幂等（`tests/test_worker_actions_api.py`）。
- 迟到 `saved_at` 不能覆盖更新发布。
- 浏览器等待超时（504）不取消后台任务。

## 未知时间报价

无 `fallbackAt` 时，非实时报价不能凭「后到」覆盖扫描价。有日期-only 备用时间时按 NYSE 收盘（`T20:00:00.000Z`）比较。不伪造备用时钟。

## 缓存穿透

C01 必须使用**未注册** `page.route` / `context.route` 的上下文。用例：`frontend-src/visual-tests/screener-http-cache.spec.mjs`，配置 `playwright.screener-cache.config.mjs`。本工作区已通过：先证明 `max-age=60` 命中（服务端计数不变），再 `cache:'reload'` 打到服务端。

## 红绿与全链

- 基线源码缺陷：`BASELINE_FINDINGS.md` + `git show 55419c8e:...`
- 修复后行为：`tests/test_screener_freshness_task_chain.py::test_e01_real_action_real_scanner_publish_and_read`（真实 POST + `scan_strength` + 发布 + GET，只替供应商下载）
- 浏览器 live：`npm --prefix frontend-src run test:screener`（独立 FastAPI + Vite live；供应商边界仍是替身）

## 未完成 / 需在最终 SHA 上重跑

见 `TEST_REPORT.md`。生产构建同步 `frontend/`、完整既有 Playwright（review/quotes/audit/visual）、容器镜像与离线 smoke，必须在最终候选提交上留下日志。提交后又改源码则旧日志作废。

**只提交 PR，不合并，不开启自动合并，不部署。**
