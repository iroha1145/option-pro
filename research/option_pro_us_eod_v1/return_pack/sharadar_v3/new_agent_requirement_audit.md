# 新 agent 真实小样本交接审计

审查锚点（开始时实际 head）：`0424b41e0efab7b5502ba61293ab132c5c60d42b`。其实现 head 为 `54f9e874989958dcf5fd13c4f60484a7cd4637e2`。本轮先读最新 head，无他人新提交。分支 `cursor/sharadar-data-first-4939`，PR #176。#174/#176 未合并，未切 main。

本 agent：`bc-6bfc9bb3-47ca-4f00-a716-b6e3100370aa`（新建，不是 `bc-aa6cc6a9-851f-46b3-80a2-31a6b8f34939` 的延续）。实现 head：`4395a09fe5f0c79c23864a570c9e6908fbdb14f1`。

未跑因子/收益/标签、未调参、未回 Yahoo、未自动购买、未启动全市场 bulk。`executed_backtests=0`。留出区 `2024-07-01` 起仍封存。已完成的 merged 内容链与 generic merger 单位语义保持冻结。

## 1 同进程凭据布尔

同一进程输出：

```
{'credential_present': False}
```

| 项 | 值 |
| --- | --- |
| 新 agent | `bc-6bfc9bb3-47ca-4f00-a716-b6e3100370aa` |
| code_sha | `4395a09fe5f0c79c23864a570c9e6908fbdb14f1` |
| source_channel | 进程环境变量 |
| 解释器 | `/usr/bin/python3` 与 `/workspace/.venv/bin/python`，均 3.12.3，均 False |
| 变量名 | `SHARADAR_API_KEY` |
| 变量是否存在于 os.environ | 否（不是空字符串） |
| 名字含 SHARADAR 的变量 | `[]` |
| 所选环境 | `3c72cd5b-a6e8-11f1-a7d1-d6b4613131ce`（Repository / REPO_FILE_OBSERVED） |
| build | `bld-20260918-d9501677-79fb-4728-b006-c505f12f30a5`，git_setup=reuse |
| environment.json | 无 secrets 字段 |
| secrets.env.example | 未列出 SHARADAR_API_KEY |
| 本机 secrets 文件 | 不存在 |

旧 agent 的「运行中会话收不到后来添加的 Secret」解释**不适用于本进程**：这是新建 agent。官方说明仍是 Secret 在启动时注入（https://cursor.com/docs/cloud-agent Troubleshooting / My secrets aren't available；Runtime vs Build：https://cursor.com/docs/cloud-agent/security-network）。因此剩余原因是环境/权限/平台注入：未在该 workspace/team/environment 配 Runtime Secret、只配了 Build Secret、名字不一致、或 Secrets 页无权限。这不是选股研究代码问题。未猜凭据，未检索聊天/旧工件，未输出其他变量。

真实网络任务到此停止。终态 `AUTH_REQUIRED`。不断言订阅是否存在。

## 2 真实小样本

入口：`research/option_pro_us_eod_v1/scripts/run_authorized_sharadar_samples.py`。复用 `SharadarClient.fetch_page`，不调用会启动全量的 data gate。

因凭据为 false，`fetch_page` 在发请求前返回 `AUTH_REQUIRED`。`real_supplier_requests=0`，`mock_requests=0`，`cache_reads=0`。

| 表 | 样本 | 非空行 | 日期 | 权限/身份 |
| --- | --- | --- | --- | --- |
| stocks | MSFT | 0 | 窗口 2024-06-24..2024-06-28 未取到 | AUTH_REQUIRED |
| funds | SPY | 0 | 同上 | AUTH_REQUIRED |
| tickers | MSFT, SPY, BBBY | 0 | 未发送日期参数 | AUTH_REQUIRED |
| actions | 同上窗口 | 0 | 空结果本可合法，但本次是未授权，不是空页证据 | AUTH_REQUIRED |
| 历史身份 | 已登记 BBBY | 0 | 主表/永久身份未核对 | AUTH_REQUIRED；不假装退市案例已通过 |

授权落库目录（未写入，因无行）：`/home/ubuntu/optix-data/authorized_sharadar_samples`。不覆盖 mock 或 `sharadar_raw` 全量库。样本数量不足，全量验收门槛不降低。

仍阻塞的字段/权限：进程中不存在 `SHARADAR_API_KEY`。

## 3 经济证据入口防护（不阻止原始下载）

`local_probes.json` 原先 12/14。对本仓库真实模块重放 14/14：

- `positive_infinity_must_be_rejected`：`inf` / `Infinity` / `1e309` 经 `finite()` 拒绝，`value=None`，`raw_value` 保留原文，`rejected_reason=no_positive_finite_value`，`accepted_as_cash_consideration=false`。
- `unverified_share_basis_must_not_be_cash_evidence`：空 ticker + 有 security 时 `share_basis=unverified`，无显式 `verified_permaticker` / `verified_permanent_identity` 证明则拒绝。`no_contradiction_found` 不算证明。

既有 merged 内容链对照、generic merger 0.4 不当现金、以及 closeout/connect/finish 回归保持。本地相关套件 86 passed。

## 4 当前 head CI（查询时刻 2026-09-19T12:37:37Z）

| SHA | 事件 | run | 当时状态 |
| --- | --- | --- | --- |
| `4395a09f`（本轮实现） | push | 35443385399 | in_progress |
| `4395a09f` | pull_request | 35443389415 | pending |
| `0424b41e`（开始时 head） | push | 35442168336 | success |
| `0424b41e` | pull_request | 35442170733 | in_progress |
| `54f9e874` | push | 35440601553 | success |
| `54f9e874` | pull_request | 35440604737 | failure（视觉步骤超时；不作本轮根因证明） |

未关测试，未加超时，无无止境文档 stamp。详情见 `algorithm_sharadar_real_sample_ci.json`。
