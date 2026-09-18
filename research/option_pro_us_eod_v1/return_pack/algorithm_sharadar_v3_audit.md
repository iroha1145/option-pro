# Sharadar 数据阶段回传

当前 head 由 git 记录。新 feature version 为 `us-eod-research-features-v1.6`。
旧 B0/R1/R1b/R2/Freeze/固定矩阵标为 `CONTROL_CURRENT_LIST_214`，不回写历史 JSON。
213 VALID / 1 INSUFFICIENT(CRWV) 与 214 名单差异单独保留。

运行时 `SHARADAR_API_KEY` 缺失，真实拉取停在 `AUTH_REQUIRED`。
未读取聊天凭据，未向 Nasdaq Data Link 或 Massive 试送密钥，未回退 Yahoo。
官方渠道实现为 `https://api.sharadar.com/v1.0/data/<table>`。
分页默认 10000 不是全市场；bulk 重定向日志去凭据。

第 7 节：母组分位改为同轨母组≥20 有限成员时用母组秩；B 突破使用冻结首日 rvol，缺失首日 rvol/clv 拒绝。
旧小池结果不按新定义重算，不新开权重搜索。

研究头 `431bc792` 的 GitHub CI 已通过：
- push https://github.com/iroha1145/option-pro/actions/runs/35377688980
- PR https://github.com/iroha1145/option-pro/actions/runs/35377695198

本地 pytest：`3976 passed, 6 skipped`。本稿 stamp 只追加 CI 记录，不回写 B0/R1/R1b/R2/Freeze/就绪/固定矩阵。

终态：`AUTH_REQUIRED`。这不是策略赢家状态。
