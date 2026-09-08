# 复审说明

2026-09-08 的复审发现、修复和新证据见 [PR148_REVIEW.md](PR148_REVIEW.md)。后续提交以该记录及当前持续集成结果为准。

## 方案

采用 **方案 B**：保留按参数分文件的快照，补齐新鲜度裁决、管理员任务链和有上限的近期变体刷新。扫描器本身已先评完整宇宙再过滤，因此没有重写成共享全池基础快照。

## 最危险的共享模块

- `frontend-src/src/api/marketRead.ts`：个股抽屉、选股读回共用。force 不再合流旧 GET，并加 `cache:'reload'`。429 退避与身份世代隔离保留；精确失效只用 `resetMarketReadPaths`。
- `frontend-src/src/lib/liveQuotes.ts` / `LiveQuote.tsx`：全站报价标签与比价。选股备用显示「扫描价」，其他页面显示「参考价」。准确成交时间相等时采用报价；只有日线日期时按纽约日期保守比较，不推算固定收盘时刻。
- `backend/app/worker/tasks.py` `StrengthRefreshTask`：定时路径额外刷最多 4 个变体；变体降级与异常都进入错误列表和部分失败状态，默认定时截止时间不变。

## 新鲜度政策

见 `DESIGN_DECISIONS.md`。审查时请核对：未知时间不会被写成当前时间；今天写盘 + 旧日线必须 stale/historical；休市合法不变不是失败。

## 任务去重与超时

- 前端同参数恢复 `sessionStorage` 中的 `requestId`。
- 后端 worker action 仍有既有冷却/幂等（`tests/test_worker_actions_api.py`）。
- 迟到 `saved_at` 不能覆盖更新发布。
- 浏览器等待超时（504）不取消后台任务。

## 未知时间报价

无 `fallbackAt` 时，非实时报价不能凭「后到」覆盖扫描价。只有交易日的备用价格保留原日期，不虚构具体时刻；后续纽约日期的有效报价可替换。异常未来备用时间不压制正常报价。

## 缓存穿透

C01 必须使用**未注册** `page.route` / `context.route` 的上下文。用例：`frontend-src/visual-tests/screener-http-cache.spec.mjs`。本工作区已通过：先证明 `max-age=60` 命中，再 `cache:'reload'` 打到服务端。

## 红绿与全链

- 基线源码缺陷：`BASELINE_FINDINGS.md` + `git show 55419c8e:...`
- 修复后行为：`tests/test_screener_freshness_task_chain.py::test_e01_real_action_real_scanner_publish_and_read`
- 浏览器 live：`npm --prefix frontend-src run test:screener`
- 既有 quotes/audit/review 已在本工作区通过。
- 完整 F05：GitHub Actions `9d92fb15` 通过（push 34145844172，PR 34145846308），含 `test:screener`、compose/镜像/WAL、`test:visual`。
- R1/R2 候选 `68740a31` 完整检查通过（push 34185808856，PR 34185810827），后端 3325、前端 822、浏览器 193。

## 审查时仍须知道

- 未做外部供应商实测；视口模拟不是真机。
- 隔离选股 Playwright 只走 `test:screener`，不要再放进默认 `test:visual`。

附件包（不进 Git）：`/opt/cursor/artifacts/screener-freshness-review/`。

**只提交 PR，不合并，不开启自动合并，不部署。**
