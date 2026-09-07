# 选股数据新鲜度修复：测试报告

**状态：部分执行。未跑完的强制项保持 NOT_RUN / BLOCKED，不记作通过。**

## 1. 本次运行身份

| 项目 | 实际值 |
|---|---|
| run_id | local-isolated-20260907 |
| base_sha | `55419c8e0f5822457f140761de98c4aa90e10c6a` |
| tested_sha | 见提交后的 PR HEAD（源码测试在工作区未提交时已跑通 pytest/前端单测） |
| pr_head_sha | 开 PR 后回填 |
| CI merge SHA | 无 |
| UTC 开始 | 2026-09-07（隔离 Cloud Agent 环境） |
| 系统 | Linux x86_64，Ubuntu 24.04 系 |
| Python / Node | Python 3.12.3（CI 钉 3.12.13）；Node 22.14.0（CI 钉 22.17.1） |
| 网络 / 供应商 | 应用测试使用本地替身；未使用生产密钥 |
| 是否接触生产服务或密钥 | 否 |
| 真实供应商只读实测 | 未进行 |
| 真实手机 | 未进行；浏览器为视口模拟 |

解释器与 CI 钉版本不完全一致。若最终 GitHub CI 与本地数字冲突，以 CI 日志为准。

## 2. 基线与红绿

| 问题 | 基线 | 修复后 | 结论 |
|---|---|---|---|
| BUG-01 | 基线 `runScan` 只对 503 提交任务 | `A01` 决策为 submit；页面 `snapshotStale: result.stale` | 确认并修复 |
| BUG-02 | `setLastScanAt(Date.now())` | 使用 `scanCompletedAt` | 确认并修复 |
| BUG-03 | 定时只刷默认文件 | 默认 + 最多 4 个近期变体；旧半导体 GET 为 historical | 确认并修复 |
| BUG-04 | 只看 26h 文件年龄 | 输入交易日/historical 政策 | 确认并修复 |
| BUG-05 | 未传 `fallbackAt` | 卡片与表传入 `priceAsOf/dailyDataThrough` | 确认并修复 |
| BUG-06 | 备用价显示「定时更新」 | 「扫描价」/「扫描价 · 日线」 | 确认并修复 |
| BUG-07 | force 可能合流旧 GET，无 reload | 内存层已复现并修复；原生 HTTP 见 C01 | 内存层确认；HTTP 层待浏览器日志 |

## 3. 已执行检查

| 层级 | 命令 | 退出码 | 结果 |
|---|---|---|---|
| Python 全量 | `PYTHONPATH=backend python -m pytest -q` | 0 | 3288 passed, 6 skipped |
| Python 编译 | `python -m compileall -q backend/app` | 0 | 通过 |
| 前端行为测试 | `node --experimental-strip-types --test frontend-src/tests/*.test.mjs` | 0 | 789 passed, 1 skipped |
| 静态断言 | `node frontend-src/tests/static_assertions.mjs` | 0 | 通过（检查的是当时已提交的 `frontend/`） |
| 代码规范 | `npm --prefix frontend-src run lint` | 0 | 0 error；2 个既有 warning（`FeedPanel.tsx`） |
| 选股 Playwright | `npx playwright test --config=playwright.screener.config.mjs` | 0 | 5 passed（F01 768 zh/en/ja；F02 1440/390 live 任务链） |
| 原生 HTTP 缓存 | `npx playwright test --config=playwright.screener-cache.config.mjs` | 0 | 1 passed（先命中 max-age，再 `cache:reload`） |
| 既有 review（含 indicators） | `npm --prefix frontend-src run test:review` | 0 | 72 + 10 passed |
| 既有 quotes/audit/visual | 各 npm script | — | NOT_RUN |
| 生产构建与 `diff -r frontend-src/dist frontend` | `VITE_API_MODE=live npm run build --prefix frontend-src` 后同步 `frontend/` | 0 | 已同步，`diff -r` 无差异 |
| 依赖锁 / pip_audit | CI 第 6 节 | — | NOT_RUN |
| 容器 / compose / 离线 smoke | CI 后半 | — | NOT_RUN |
| 外部供应商实测 | 可选 | — | 未进行 |

## 4. 强制矩阵映射

| 编号 | 用例 | 状态 |
|---|---|---|
| A01 | `screener-freshness-flow.test.mjs` `A01 owner click on a stale 200 snapshot submits a refresh` | PASS（单元决策+源码契约） |
| A02 | 同上 `A02 matching fresh snapshot is reused...` | PASS |
| A03 | 同上 `A03 missing snapshot submits only for the owner` | PASS |
| A04 | 前端 visitors never submit；`test_a04_visitor_cannot_submit_strength_refresh`；`test_a04_visitor_get_does_not_touch_variant_mtime` | PASS |
| A05 | `A05 signed-in customer is not treated as owner`；既有 `market-read` 身份世代 | PASS（逻辑） |
| A06 | `tests/test_worker_actions_api.py` `test_manual_action_reports_active_and_cooldown_states` | PASS（既有） |
| A07 | `A07 later generation must win` + `Screener.tsx` `scanSeq` | PASS（逻辑）；浏览器迟到写回待 E2E |
| A08 | `test_manual_actions_queue_and_reuse_the_same_minute` | PASS（API 合流）；10 次点击计数待浏览器 |
| A09 | `A09 pending task is recovered...` | PASS（sessionStorage） |
| B01 | `test_b01_fresh_default_does_not_make_old_semiconductor_current` | PASS |
| B02 | `test_b02_payload_separates_universe_from_returned_top`；E01 `universe_count` | PASS |
| B03 | `test_b03_sector_filter_does_not_rescore_the_same_ticker` | PASS |
| B04 | `test_ttl_expiry_keeps_body_semantics...` | PASS |
| B05 | `test_b05_today_write_with_old_bars_is_not_fresh`；`test_recent_save_cannot_hide_old_daily_input` | PASS |
| B06 | `test_independence_day_observed...`；`test_early_close_and_dst_boundaries`；`test_b06_tokyo_and_new_york_share_the_session_date` | PASS |
| B07 | 扫描器按行 skipped；发布闸拒绝整池失败 | 部分（E01 覆盖成功路径） |
| B08 | `test_failed_provider_does_not_publish_empty_fresh_snapshot`；`test_total_provider_failure_is_not_publishable` | PASS |
| B09 | `test_b09_corrupt_and_mismatched_snapshots_stay_unavailable`；`test_b09_unknown_snapshot_does_not_invent_now` | PASS |
| B10 | E01 第二次 `run_for_actions` 分数/日期相同 | PASS |
| B11 | `test_late_write_cannot_replace_newer_publish` | PASS |
| C01 | `screener-http-cache.spec.mjs` `cached GET is reused, then cache:reload reaches the server` | PASS |
| C02 | `market-read.test.mjs` 旧 in-flight 不回写 | PASS（内存层） |
| C03 | ETag `version_key` 含 source_status / score_data_through | 部分（实现+既有 snapshot 测试） |
| C04 | `manual path invalidation keeps the shared provider backoff` | PASS |
| C05 | `Screener.tsx` 45s + visibilityState | 实现已加；浏览器 NOT_RUN |
| C06 | 断网恢复 | NOT_RUN |
| D01–D07 | `live-quotes-behavior.test.mjs` + `screener-freshness-flow` | PASS（单元）；移动端可见日期待 Playwright |
| E01 | `test_e01_real_action_real_scanner_publish_and_read` | PASS |
| E02 | 既有 worker action 状态测试 | 部分 |
| E03–E06 | 参数不匹配 409；发布闸 | 部分 / NOT_RUN |
| F01–F02 | `screener-freshness.spec.mjs`（视口模拟，非真机） | PASS |
| F03 | `ui-review.spec.mjs` 全研究页 390/768/1440 | PASS（mock 布局）；quotes/audit 未跑 |
| F04 | 生产构建与产物 diff | PASS（本工作区）；须以最终 SHA 的 CI 复核 |
| F05 | 完整 CI / 镜像 | NOT_RUN |

## 5. 三条证据链

1. **管理员旧半导体**：pytest E01 真实任务链 + B01 文件场景 + Playwright F02（live Vite → 隔离 API → 真实 `scan_strength`，供应商替身）。
2. **上游失败**：`test_failed_provider_does_not_publish_empty_fresh_snapshot` 不写新鲜空文件，任务 `degraded`。
3. **休市/输入不变**：`test_closed_market_with_matching_session_is_fresh`；E01 重算分数不变。

## 6. 已知局限

- 本地 Python/Node 微版本低于 CI 钉版本。
- 既有 `test:review` / `test:quotes` / `test:audit` / `test:visual` 与容器阶段仍须在最终 SHA 的 GitHub CI 上留下日志。
- `review_manifest.json` 完整机器清单尚未从本工作区导出到附件包。
- 未进行外部供应商实测，不能写成「线上行情源已验证」。
