# 选股数据新鲜度修复：测试报告

**状态：部分执行。未跑完的强制项保持 NOT_RUN / BLOCKED，不记作通过。**

## 1. 本次运行身份

| 项目 | 实际值 |
|---|---|
| run_id | local-isolated-20260907 |
| base_sha | `55419c8e0f5822457f140761de98c4aa90e10c6a` |
| tested_sha | `39f96ce2ad6bf133b28ff5cdccaf2824aaa4b14b`（本工作区最后一次选股 Playwright / 构建同步后的 HEAD） |
| pr_head_sha | 以 GitHub PR #148 为准；撰写时与 `tested_sha` 相同 |
| CI merge SHA | 无；完整容器阶段等 GitHub Actions |
| UTC | 2026-09-07（隔离 Cloud Agent 环境） |
| 系统 | Linux x86_64，Ubuntu 24.04 系 |
| Python / Node | Python 3.12.3（CI 钉 3.12.13）；Node 22.14.0（CI 钉 22.17.1）；npm 10.9.7 |
| 网络 / 供应商 | 应用测试使用本地替身；未使用生产密钥 |
| 是否接触生产服务或密钥 | 否 |
| 真实供应商只读实测 | 未进行 |
| 真实手机 | 未进行；浏览器为视口模拟 |
| 前端入口摘要 | `frontend/index.html` sha256 `deeb94f126a1cf6484042e70a77a5bca922dbdee74c3c677e5c30acd0bd340c8` |

解释器与 CI 钉版本不完全一致。若最终 GitHub CI 与本地数字冲突，以 CI 日志为准。提交后又改源码则旧日志不能冒充新 SHA。

## 2. 基线与红绿

| 问题 | 基线 | 修复后 | 结论 |
|---|---|---|---|
| BUG-01 | 基线 `runScan` 只对 503 提交任务 | stale/unknown/historical 对 owner 提交任务 | 确认并修复 |
| BUG-02 | `setLastScanAt(Date.now())` | `scanCompletedAt`；页头同时显示读取时间 | 确认并修复 |
| BUG-03 | 定时只刷默认文件 | 默认 + 最多 4 个近期变体；旧半导体 GET 为 historical | 确认并修复 |
| BUG-04 | 只看 26h 文件年龄 | 输入交易日 / historical 政策 | 确认并修复 |
| BUG-05 | 未传 `fallbackAt` | 卡片与表传入 `priceAsOf/dailyDataThrough` | 确认并修复 |
| BUG-06 | 备用价显示「定时更新」 | 「扫描价」/「扫描价 · 日线」；无报价时仍显示 | 确认并修复 |
| BUG-07 | force 可能合流旧 GET，无 reload | 内存层不合流 + `cache:'reload'`；C01 先命中再穿透 | 确认并修复 |

## 3. 已执行检查

| 层级 | 命令 | 退出码 | 结果 |
|---|---|---|---|
| Python 全量 | `PYTHONPATH=backend python -m pytest -q` | 0 | **3289 passed, 6 skipped**（含 C03） |
| Python 编译 | `python -m compileall -q backend/app` | 0 | 通过 |
| 前端行为测试 | `node --experimental-strip-types --test frontend-src/tests/*.test.mjs` | 0 | **791 passed** |
| 静态断言 | `node frontend-src/tests/static_assertions.mjs` | 0 | 通过（`frontend/` 与 live dist 一致） |
| 代码规范 | `npm --prefix frontend-src run lint` | 0 | 0 error；2 个既有 warning（`FeedPanel.tsx`） |
| 选股 Playwright | `npm --prefix frontend-src run test:screener` | 0 | **14 + 1 passed**（F01 12 组合；F02 1440/390；C01） |
| 既有 review | `npm --prefix frontend-src run test:review` | 0 | 72 + 10 passed |
| 既有 quotes | `npm --prefix frontend-src run test:quotes` | 0 | **21 passed** |
| 既有 audit | `npm --prefix frontend-src run test:audit` | 0 | **6 passed** |
| 既有 visual | `npm --prefix frontend-src run test:visual` | — | NOT_RUN（需要 compose :2000；本环境无 Docker） |
| 生产构建与产物 | `VITE_API_MODE=live npm run build --prefix frontend-src` 后 `diff -r` | 0 | 已同步 |
| 锁文件源哈希 | CI 第 6 节两条 `grep -qx` | 0 | runtime / ci 锁匹配 |
| pip_audit | `pip_audit --require-hashes --disable-pip -r backend/requirements.txt` | 0 | No known vulnerabilities |
| 脚本语法 | `bash -n setup.sh personal.sh scripts/compose.sh scripts/deploy.sh scripts/lock-dependencies.sh` | 0 | 通过 |
| 容器 / compose | CI 后半 | — | BLOCKED（本环境无 Docker）；等 GitHub CI |
| 外部供应商实测 | 可选 | — | 未进行 |

## 4. 强制矩阵映射

| 编号 | 用例 | 状态 |
|---|---|---|
| A01 | `screener-freshness-flow` A01 + Playwright F02 真实 POST/`scan_strength` | PASS |
| A02 | A02 单元；F02 二次扫描 0.0s「使用已有评分」 | PASS |
| A03 | A03 单元 | PASS |
| A04 | 前端 visitors never submit；`test_a04_*` | PASS |
| A05 | A05 单元 + market-read 身份世代 | PASS（逻辑） |
| A06 | `test_manual_action_reports_active_and_cooldown_states` | PASS（既有 API） |
| A07 | A07 路径按参数隔离 + `scanSeq` | PASS（逻辑）；浏览器迟到写回未单独 E2E |
| A08 | `test_manual_actions_queue_and_reuse_the_same_minute` | PASS（API）；10 次点击浏览器计数未做 |
| A09 | A09 sessionStorage 恢复 | PASS |
| B01 | `test_b01_*` | PASS |
| B02 | `test_b02_*`；E01 `universe_count` | PASS |
| B03 | `test_b03_sector_filter_does_not_rescore_the_same_ticker` | PASS |
| B04 | `test_ttl_expiry_keeps_body_semantics...` | PASS |
| B05 | `test_b05_*`；`test_recent_save_cannot_hide_old_daily_input` | PASS |
| B06 | 假日/提前收盘/DST/东京纽约同会话日 | PASS |
| B07 | 扫描器按行 skipped；发布闸 | 部分 |
| B08 | 供应商全失败不发布新鲜空结果 | PASS |
| B09 | 损坏/未知快照不填 now | PASS |
| B10 | E01 第二次重算分数/日期相同 | PASS |
| B11 | `test_late_write_cannot_replace_newer_publish` | PASS |
| C01 | `screener-http-cache.spec.mjs`（无 `page.route`） | PASS |
| C02 | `market-read.test.mjs` force 不合流 | PASS（内存层） |
| C03 | `test_c03_etag_304_does_not_invent_a_new_data_date` | PASS |
| C04 | `manual path invalidation keeps the shared provider backoff` | PASS |
| C05 | `shouldDiscoverPublishedScan` 单元；Screener 45s + visibility | PASS（逻辑）；浏览器隐藏计数未做 |
| C06 | 断网恢复 | NOT_RUN |
| D01–D05 | `live-quotes-behavior.test.mjs` | PASS（单元） |
| D06 | 评分日期与报价标签独立 | PASS |
| D07 | F02 390 卡片「扫描价 2026-09-04」 | PASS（视口模拟） |
| E01 | `test_e01_real_action_real_scanner_publish_and_read` + F02 | PASS |
| E02 | 既有 worker action 状态测试 | 部分 |
| E03 | 前端参数不匹配 409 | 部分（源码路径） |
| E04 | 原子写 + 变体上限既有逻辑 | 部分 |
| E05 | 访客 GET 不 utime / 不 POST | 部分（pytest，非压测） |
| E06 | E01 / F02 参数哈希与页面日期可对应 | 部分 |
| F01 | 320/390/768/1440 × zh/en/ja | PASS（视口模拟） |
| F02 | 1440 / 390 live 任务链 | PASS |
| F03 | review + quotes + audit | PASS（visual 未跑） |
| F04 | live 构建 + `diff -r` | PASS（本工作区） |
| F05 | 完整 CI / 镜像 | BLOCKED / 等 GitHub Actions |

## 5. 三条证据链

1. **管理员旧半导体**：pytest E01 + B01 + Playwright F02。截图：`/opt/cursor/artifacts/screener-freshness/desktop-1440-before.png`、`desktop-1440-after.png`（NVDA 76.0、评分依据 2026-09-04、扫描价）、`mobile-390-after.png`、`mobile-390-nvda-card.png`。
2. **上游失败**：`test_failed_provider_does_not_publish_empty_fresh_snapshot`，任务 `degraded`，不写新鲜空文件。
3. **休市/输入不变**：`test_closed_market_with_matching_session_is_fresh`；E01 重算分数不变。

隔离 API 的市场形态侧栏为空对象，桌面空态可见「强弱价差 NaN」——这是替身夹具，不是生产评分公式改动。

## 6. 已知局限

- 本地 Python/Node 微版本低于 CI 钉版本。
- 本环境无 Docker：`test:visual` 与 compose 镜像/WAL/离线 smoke 未跑，记 BLOCKED。
- A07/A08 浏览器竞态计数、C06 断网、部分 E02–E06 未做完整浏览器证明。
- 未进行外部供应商实测，不能写成「线上行情源已验证」。
- 浏览器为视口模拟，不是真机。
