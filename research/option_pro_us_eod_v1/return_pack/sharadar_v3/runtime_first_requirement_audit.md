# RUNTIME_FIRST_AND_TWO_FIXES 逐条审计

审查锚点：`50495405ce085e898bf260c784aff3da91bb5b5f`（docs-only stamp）。其实现 head 为 `b5c6669c1d953c95cb45f960af1b025f8a4df763`。本轮先读最新 head 再改，未覆盖既有工作。分支 `cursor/sharadar-data-first-4939`，PR #176。#174/#176 未合并，未切 main。

本轮不新增研究轮次：未跑小池、因子、权重网格、消融、邻域、冻结验证、固定矩阵，`executed_backtests=0`，留出区 2024-07-01 起仍封存。

## 1 先完成运行环境验收

在真正运行数据脚本的进程里只输出布尔值：

```
{'credential_present': False}
```

未打印值、长度、前后缀、完整环境变量表或含密钥 URL；未从聊天或旧工件检索密钥；未把密钥写入 git 或 CI 配置。测试里的 `monkeypatch.setenv` 只是 mock，不当作真实授权。

因此本轮真实供应商请求为 0，真实小样本未执行。运行环境记录（`sharadar_v3/runtime_credential_report.json`）：

| 项 | 值 |
| --- | --- |
| 工作区 | `/workspace` |
| 分支 / head | `cursor/sharadar-data-first-4939` / 见 `algorithm_sharadar_runtime_first_head.json` |
| 解释器 | `/usr/bin/python3` 与 `/workspace/.venv/bin/python`，均 Python 3.12.3，均 `credential_present=False` |
| 名字含 SHARADAR 的环境变量 | `[]` |
| 所选环境 | `3c72cd5b-a6e8-11f1-a7d1-d6b4613131ce`（Repository / REPO_FILE_OBSERVED），build `bld-20260916-2add662d-934b-4d72-9014-f21cabd67b85` |
| 进程启动方式 | Cursor Cloud Agent VM 启动 → `.cursor/install.sh` → 本 agent 会话 |
| 作用域 | 进程环境变量。本 VM 的进程环境中不存在 `SHARADAR_API_KEY` |
| 本 agent 是否为注入 Secret 之后新建 | 否。`bc-aa6cc6a9-851f-46b3-80a2-31a6b8f34939` 创建于本轮任务之前，是同一会话的延续 |

按官方说明，Secret 在启动时注入，运行中的旧 agent 不会取得后来添加的 Secret（`https://cursor.com/docs/cloud-agent`；Runtime 与 Build Secret 的区别见 `https://cursor.com/docs/cloud-agent/security-network`）。本次是在旧 agent 里继续，即使负责人已在正确 workspace/team 与所选环境里配置了 Runtime Secret，这个进程也不会看到它。

真实网络任务到此结束，终态 `AUTH_REQUIRED`。不断言用户没有订阅，也不把 `AUTH_REQUIRED` 当作继续堆代码的理由：本轮只做第 2 节点名的两条修正。

## 2A 消费者读取的 merged 文件按内容校验

原状态：`verify_ingested_state` 验了每个已提交页的 sha256，但 merged 只 `count_jsonl`，主键索引也只比 count。页与索引完好、merged 行数与主键都一样、只把某一行 `close` 从 10 改成 99 时，仍返回 `READ_OK`，消费者读到 99。

改法（`sharadar_store.py` / `sharadar_bulk.py` / `sharadar.py`）：

- 合并文件是 append-only，于是按行折叠一条内容链：`merged-content-chain-v1`。写入时每行一次哈希，校验时一次流式回放。`merge_committed` 与 `append_merged` 都返回链状态，`advance_checkpoint` 把 `{version, rows, chain_sha256}` 绑进 checkpoint。不整表载入内存，也没有为此重写下载架构。
- `verify_ingested_state` 复用前对**实际被消费者读取的那个文件**重算链并比对。行数检查保留，但不再是唯一依据。
- 摘要缺失的旧工件不得自签：`merged_content` 不存在或版本不符时报 `merged_content_unrecorded`，状态非 `READ_OK`，需从仍可信的 pages 重建后再绑定摘要。
- 内容变化时先尝试从已验证页自动修复（`_rebuild_derived` 重建 merged + 主键索引并重新绑定摘要并落盘 checkpoint）；页本身不可信则返回 `CORRUPT` / `PARTIAL`。改过的 merged 不会被保留成 `READ_OK`。
- 分页路径同样纳入：`_ensure_merged` 现在把内容链和行数一起作为重建条件，重建原因记在 `merged_content_rebuilt_because`。

| 场景 | 结果 | 回归 |
| --- | --- | --- |
| 完好 | `READ_OK`，`problems=[]` | `test_bulk_first_ingest_records_a_verifiable_payload` |
| 行数与主键不变、单行内容被改 | `CORRUPT`，`merged_content_mismatch`，`merged_rows == expected_rows == 5`，`key_index_rows=5`；复用时从已验证页修好，消费者读回 10.0 | `test_same_row_count_and_keys_with_changed_content_is_not_read_ok` |
| 无摘要的旧工件 + 内容被改 | 非 `READ_OK`，`merged_content_unrecorded`；重建后绑定摘要 | `test_merged_file_without_a_recorded_digest_cannot_vouch_for_itself` |
| 行序被打乱 | 行数相同、链不同 → `MISMATCH` | `test_digest_tracks_content_where_a_row_count_cannot` |
| 分页下载后 merged 被改 | 复用时重建，`merged_content_rebuilt_because=MISMATCH` | `test_paged_download_also_rebuilds_an_edited_merged_file` |
| 缺页 / 改页 / 缺 merged / 只剩 checkpoint / 归档变化 / 恢复 | 原行为保留 | `test_research_eod_v1_closeout_gate.py` 全部 12 条仍通过 |

代价：校验一次 = 对 merged 的一次顺序读，与原来 `count_jsonl` 同量级。实测离线 54,412 行 stocks 表，完好判定与篡改判定都是 0.05s。

审阅方提到的 `tests_repo/test_pr176_last_two_boundaries.py` 是待执行的仓库回归草案，本轮按真实接口整合进 `tests/test_research_eod_v1_pr176_boundaries.py`（12 条），在本仓库实际运行。

## 2B 普通 merger 数字不自动具有美元现金结算语义

原状态：`classify_terminal` 对 `action.startswith('merger')` / `startswith('acquisition')` 的正数一律汇入 `cash_values`，`evaluate_delist_fixture` 再按自产的 `actions.cash_consideration` reason 释放经济执行。于是 `merger` 上的 `0.4` 被当成每股 0.4 美元的结算价。

改法（`sharadar_schema.py` / `sharadar_identity.py` / `sharadar_acceptance.py`）：

- 单位语义单独成表并版本化：`ACTION_VALUE_SEMANTICS_VERSION = "sharadar-action-value-units-v1"`。
  - `ACTION_CASH_CONSIDERATION = {acquisitioncash, acquisitionbycash}` → `usd_per_share`。
  - `ACTION_ACQUISITION_UNIT_UNVERIFIED = {acquisitionby, acquired, merger, mergerfrom, takeprivate}` → 单位 `unverified`。
  - `ACTION_PARTIAL_CONSIDERATION = {acquisitionelectcash, acquisitionelectstock, cvr, contingentvaluerights, contingentconsideration}` → 只是一条腿。
  - `ACTION_ACQUISITION_STOCK` 不变。
- `action_value_evidence()` 对每条动作行产出可追溯记录：原始 `action` / `date` / `ticker` / `raw_value` / 解析值 / `unit` / `unit_source` / 对应证券股数基准 `share_basis` / `semantics_version` / 是否被接纳 / 拒绝原因，并显式带 `not_assumed_usd`、`not_assumed_exchange_ratio`。未证明单位的数值原样保留，既不当美元也不猜成换股比率。
- 现金对价须同时满足：动作码属现金词表（供应商语义）、单位为 `usd_per_share`、动作行属于被结算的那只证券（股数基准）、且存在该终止事件。任一不满足则分类为 `TERMINAL_UNKNOWN`、经济能力 `UNSUPPORTED`。
- reason 字符串不再充当证据：`evaluate_delist_fixture` 的 `settled` 由结构化 `cash_consideration` 决定，并在 `settlement_evidence_source` 里给出原始动作；未定价动作列在 `unpriced_actions`。
- 规则分版 `delist-terminal-rule-v3`（v2 只区分「标签确定」与「有结算证据」；v3 追加「数字没有单位证明就不是现金」）。`IDENTITY_MIN_CONCRETE_TERMINALS` 仍为 12，未为凑案例或凑绿色改门槛。
- 身份解析与经济终值仍然分开：`merger` 案例的身份照常解析、原始价格照常落库、转换照常进行，只有 EXECUTION 保持 blocked。

| 情形 | label | reason | live | concrete | 备注 |
| --- | --- | --- | --- | --- | --- |
| `merger` 0.4 | `TERMINAL_UNKNOWN` | `acquisition_value_unit_unverified` | `UNSUPPORTED` | false | 0.4 原样留在 `unpriced_actions` |
| `acquisitionby` / `acquired` / `takeprivate` / `mergerfrom` / 未知 `acquisition*` | `TERMINAL_UNKNOWN` | 同上 | `UNSUPPORTED` | false | 数值保留 |
| `acquisitioncash` 12.0（纯现金对照） | `acquisition_cash` | `actions.cash_consideration` | `PASS` | true | `settlement_evidence_source.unit=usd_per_share` |
| `acquisitioncash` + `acquisitionstock`（现金+股票对照） | `TERMINAL_UNKNOWN` | `acquisition_stock_or_mixed` | `UNSUPPORTED` | false | 混合对价不因缺另一条动作而宣告完整 |
| `acquisitionelectcash` 单腿 / `acquisitioncash` + `cvr` | `TERMINAL_UNKNOWN` | `acquisition_consideration_incomplete` | `UNSUPPORTED` | false | 选择权与 CVR 同理 |
| 破产 + 最后报价 7.0（最后报价对照） | `bankruptcy_last_trade` | `last_trade` | `PASS` | false | 观察量，`value_unit=usd_per_share_observed_quote` |
| 现金动作挂在别的证券上 | `TERMINAL_UNKNOWN` | `acquisition_action_on_another_security` | `UNSUPPORTED` | false | `share_basis=mismatched` |
| 空值 / 非数值 / 非正值 | `TERMINAL_UNKNOWN` | `acquisition_without_cash` | — | false | 沿用既有数据契约，不强解 |

离线整链（`fake_sharadar_server.py`，两种模式都跑）：16 个身份全部解析，`determinate_terminal=15`（阈值 12，未改），`settlement_evidenced=9`；1 个 `acquisitionby` 案例与 6 个仅有最后报价的破产案例把 EXECUTION 锁在 `UNSUPPORTED`，原始数据层仍 `DATA_GATE_ACCEPTED`。

`fake_sharadar_server.py` 的合成数据同步修正：`expected_terminal=acquisition_cash` 的 fixture 现在写 `acquisitioncash`（这些案例本来就是现金收购），唯一的 `acquisition_or_unknown` fixture 保留 `acquisitionby`，让离线链同时覆盖「身份解析成功但经济门关闭」的路径。这是把测试替身的数据改对，不是改验收门槛。

## 3 真实样本优先于全量

本进程 `credential_present=False`，真实请求 0，因此本节不适用。未把同一 key 发给 Nasdaq Data Link 或 Massive，未回退 Yahoo，未触发全市场 bulk。两项修正不阻止后续受控原始样本请求；在修正之前，损坏工件与不明对价都不会拿到对应的验收 PASS。

## 4 本轮交付

| 项 | 位置 |
| --- | --- |
| 凭据布尔与运行环境 | `sharadar_v3/runtime_credential_report.json` |
| 当前 SHA / CI | `algorithm_sharadar_runtime_first_head.json`、`algorithm_sharadar_runtime_first_ci.json` |
| 两项修正的回归 | `tests/test_research_eod_v1_pr176_boundaries.py`（12 条）+ 既有 `test_research_eod_v1_closeout_gate.py`（12 条）+ 全量 `python -m pytest -q` |
| 审阅探针 18 例重放 | `sharadar_v3/runtime_first_probe_replay.json` |
| 四表行数与日期、原始工件位置 | 见下表（离线链，非真实供应商数据） |
| 按能力分层的未通过项 | 见下表 |

离线链（`--mode bulk_first` 与 `--mode paged` 各跑一次，store 在仓库外）：

| 表 | 行数 | 模式 | merged 摘要 |
| --- | --- | --- | --- |
| tickers | 31 | bulk | `merged-content-chain-v1` |
| stocks | 54,412 | bulk | `merged-content-chain-v1` |
| funds | 7,296 | bulk | `merged-content-chain-v1` |
| actions | 90 | paged | `merged-content-chain-v1` |

按能力分层的未通过项：

| 层 | 状态 | 原因 |
| --- | --- | --- |
| 真实供应商接通 | `AUTH_REQUIRED` | 本进程无 `SHARADAR_API_KEY`；需 Runtime Secret + 新 agent |
| 真实四表样本 / 身份与单位证据 | 未执行 | 同上，不是代码缺陷 |
| EXECUTION（经济结算） | `UNSUPPORTED` | 仅有最后报价的破产案例与单位未证明的 merger 案例没有结算证据 |
| VOLUME_SCOPE | `UNSUPPORTED` | 会话口径未验证，未启动成交量实验 |
| 其余 7 个必需层 | 离线链 `PASS` | 合成数据的链路验证，不替代真实供应商验收 |

实现 head `54f9e874` 的 GitHub CI：

| 事件 | 运行 | 结论 |
| --- | --- | --- |
| push | https://github.com/iroha1145/option-pro/actions/runs/35440601553 | **success**（每一步均 success，含 `Run Python tests`） |
| pull_request | https://github.com/iroha1145/option-pro/actions/runs/35440604737 | failure，失败步骤为 `Capture Catalyst Desk and macro conditions visual evidence` |

PR 事件失败与本轮改动无关：同一 SHA 的 push 运行里该步骤 success；本轮未改任何 frontend 文件；该步骤 73/75 视觉测试通过，失败的一条是等一级标题 15 秒超时，不是行为断言。Python 步骤在两次运行里都是 success。详情见 `algorithm_sharadar_runtime_first_ci.json`。

停止条件：未合并、未切 main、未自动采购、未晋级、未新增算法或研究框架、留出区未解封。
