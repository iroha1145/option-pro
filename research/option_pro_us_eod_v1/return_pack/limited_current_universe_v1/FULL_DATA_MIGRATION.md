# 获得完整数据后怎么迁，而不是换一把 key

受限版和完整版共用 `extract_raw` / `compute_snapshot` / `m1_consensus` / `publish_snapshot`。分流只走数据能力与版本配置，不在每个评分函数里写 `if free_mode`。

1. **换 provider / dataset**
   新建数据版本（新 `dataset_id` + 内容哈希）。不要复用 `u_limited_current_v1` 或本轮 `dataset_hash`。Yahoo 诊断缓存与付费 PIT 行情不可互换。

2. **重建身份与主题表**
   用可审计的证券主数据（建议 `permaticker`）替换 `CURRENT_MEMBERSHIP`。补历史成员、退市并集、PIT 行业。主题输出仍在完整同轨参考池上截取。

3. **选择完整能力轨**
   能证明含分红总回报、常规时段量、未复权股数后，才退出 `PRICE_ONLY_DIAGNOSTIC` / `VENDOR_DAILY_UNVERIFIED` / `price_return_not_total_return`。G、行业残差、流动性硬门按原定义恢复，不要把本版分数改名升级。

4. **使缓存失效**
   丢掉本版 `bars.pkl`、session `scored.json` 和快照。`config_hash` / `run_signature` 必须因数据、成员政策或能力轨变化而改变。

5. **重跑固定基线**
   先跑登记的 `24×4×balanced×mid`，再决定是否做 864 全期比较。不要套用本版 20 日名单或旧 214 池权重。

6. **再谈参数修正**
   只有新数据版本上的固定基线可复现之后，才另批权重/消融。十年历史、执行账本、解封 `2024-07-01` 留出区都是后续完整版的事。

旧受限结果留档对照，禁止倒写成完整 PIT 验证通过。
