# 复审说明

## 方案

采用 **方案 B**：保留按参数分文件的快照，补齐新鲜度裁决、管理员任务链和有上限的近期变体刷新。扫描器本身已先评完整宇宙再过滤，因此没有重写成共享全池基础快照。

## 最危险的共享模块

- `frontend-src/src/api/marketRead.ts`：个股抽屉、选股读回共用。force 不再合流旧 GET，并加 `cache:'reload'`。429 退避与身份世代隔离保留；精确失效只用 `resetMarketReadPaths`。
- `frontend-src/src/lib/liveQuotes.ts` / `LiveQuote.tsx`：全站报价标签与比价。备用价不再显示「定时更新」。成交时间等于备用时间时采用报价（自选 `updatedAt` 与 `trade_at` 对齐）；日期-only 日线按 NYSE 收盘比较，盘中价不能压过当日完整日线。无 store 报价时仍显示「扫描价」和日期。
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

C01 必须使用**未注册** `page.route` / `context.route` 的上下文。用例：`frontend-src/visual-tests/screener-http-cache.spec.mjs`。本工作区已通过：先证明 `max-age=60` 命中，再 `cache:'reload'` 打到服务端。

## 红绿与全链

- 基线源码缺陷：`BASELINE_FINDINGS.md` + `git show 55419c8e:...`
- 修复后行为：`tests/test_screener_freshness_task_chain.py::test_e01_real_action_real_scanner_publish_and_read`
- 浏览器 live：`npm --prefix frontend-src run test:screener`
- 既有 quotes/audit/review 已在本工作区通过。
- 完整 F05：GitHub Actions `9d92fb15` 通过（push 34145844172，PR 34145846308），含 `test:screener`、compose/镜像/WAL、`test:visual`。

## 审查时仍须知道

- 未做外部供应商实测；视口模拟不是真机。
- 隔离选股 Playwright 只走 `test:screener`，不要再放进默认 `test:visual`。

附件包（不进 Git）：`/opt/cursor/artifacts/screener-freshness-review/`。

**只提交 PR，不合并，不开启自动合并，不部署。**
