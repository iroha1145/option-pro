# v3 规格逐条对照

复核锚点：`ec14a166`。研究头：`431bc792`。PR：#176，base=`cursor/us-eod-research-pack-4939`。

| 条款 | 证据 | 状态 |
|---|---|---|
| 停 Yahoo 864 / 权重搜索 / 冻结补跑 | `algorithm_sharadar_v3_stop.json` | PASS |
| 独立 PR，不重带 #174 工件，不合入生产 | https://github.com/iroha1145/option-pro/pull/176 | PASS |
| 凭据只读 `SHARADAR_API_KEY`；不读聊天密钥 | `provider_probe.json` `credential_present=false` | PASS |
| 只实现官网渠道 | `schema.json` `channel=api.sharadar.com` | PASS |
| 密钥缺失停 `AUTH_REQUIRED`，不回退 Yahoo | `terminal_status=AUTH_REQUIRED` | PASS |
| 表/字段/分页/bulk 去凭据 | `backend/app/services/research_eod_v1/data/sharadar.py` | PASS；活读 AUTH_REQUIRED |
| 三轨换算 | `sharadar_tracks.py` 与合成测试 | PASS |
| 身份 / 16 fixture / 终端标签 | `delist_identity_acceptance.json` | PASS；活核 AUTH_REQUIRED |
| Yahoo 对账阈值 | `source_reconciliation.json` | PASS；活对账 AUTH_REQUIRED |
| volume-scope UNKNOWN | `volume_scope_audit.json` | PASS |
| 历史预算 | `history_budget.json` | PASS；实际覆盖 AUTH_REQUIRED |
| CONTROL_CURRENT_LIST_214；213/214 不混 | `control_current_list_214_index.json` | PASS |
| 母组分位 52.6316 / 0 | `tests/test_research_eod_v1_parent_rank.py` | PASS |
| B 首日冻结 / 缺证据拒绝 | `tests/test_research_eod_v1_breakout_first_day.py` | PASS |
| feature v1.6；旧 B0 不回写 | `FEATURE_VERSION`；归档 v1.5 | PASS |
| 本地回归 | 3976 passed, 6 skipped | PASS |
| 研究头 CI | push 35377688980 / PR 35377695198 | PASS |

终态：`AUTH_REQUIRED`。不是策略赢家状态。
