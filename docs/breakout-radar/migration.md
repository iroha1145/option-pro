# 迁移与兼容

## 发布顺序

1. 部署包含新代码但保持 BREAKOUT_RADAR_ENABLED=false。
2. 停止旧 Worker，并为 `/data/optix.db` 制作可恢复备份；确认没有写进程后再升级。
3. 新 Worker 初始化 breakout-db-v3；Backend 仍不迁移和写库。
4. 验证 /ready、旧接口和突破 status=disabled。
5. 启动 Worker 的离线 --once 与单实例测试。
6. 在受控环境启用 Provider，保持 RANGE_PERSISTENCE_MODE=shadow。
7. 验证日线补全后的 20 日平均成交额门槛；缺失或低于
   BREAKOUT_MIN_AVG_DOLLAR_VOLUME 的候选不得进入 Strength 或盘中增强。
8. 验证 BREAKOUT_OPENING_RANGE_MINUTES 为 5 分钟的整数倍，且完整 K 线网格
   覆盖整个配置区间后才生成开盘区间高低点。
9. 验证 completed 原子快照、stale 降级、WAL 和 API。Provider 返回
   unavailable 时本次扫描必须标记 failed，上一 completed 快照保持可读；只有
   Provider 明确返回 active/degraded 的真实空结果才允许发布空 completed。
10. 收集影子研究；未达门槛不得启用 Range Persistence 正式权重。

## 旧 Strength

- 保留 /api/strength 路径与旧公共字段。
- 新增 score_ticker_set 只处理显式集合，默认 include_options=false。
- 新返回标记 score_scope、confidence、score_version 和 included_features。
- 旧 final_score 若仍含市场、行业或期权，必须标为 legacy_market_adjusted，
  不能填进 intrinsic_strength_score。
- 单股入口改走窄评分后保留 final_score、strength_score、classification 和
  breakdown 等兼容别名。

## 数据库迁移

- Worker 是唯一迁移执行者。
- breakout_schema_version 记录版本、校验和和应用时间。
- 每次迁移在独立事务中执行，可重复检查但不可静默重写已应用版本。
- v1 和 v2 均可直接升级到 v3；已发布的 v1/v2 校验和保持不变。事件身份、转换、
  已完成扫描和 v2 时间语义均保留。
- v3 重建事件 JSON 约束，允许最多 512 KiB；超限有效 JSON 会按固定顺序压缩
  可重建调试字段。损坏或仍超限的记录不会静默删除，而是根据结构化列重建最小
  事件，并写入 `breakout_migration_quarantine`。
- Provider 原缓存键迁入独立的 `provider_cache_key`；数据库 `snapshot_id` 加入
  `scan_run_id`，避免同一缓存桶内主键冲突。
- 升级在单一事务中执行，提交前检查外键；失败完整回滚。迁移前备份是发布门槛，
  quarantine 不是备份替代品。
- API 面对旧版本或缺库时返回 unavailable，不自动建库；旧版本会明确返回
  schema_upgrade_required、当前版本和所需版本。
- 降级回旧应用时不得让旧 Worker 写 v3 数据库；应停止写入并恢复升级前备份。

## 缓存和版本

新增版本进入缓存键，旧缓存不能跨版本复用。配置哈希、session、source date、
ticker 集合和 canonical universe 变化都产生新键。

## Range Persistence 迁移

- 旧 Pine 输出保存在 legacy_control，只供研究和对照。
- 正式字段统一 range_persistence 前缀。
- 默认 shadow，生产分、分类和排序保持逐字段不变。
- enabled 需要独立验证报告和显式配置变更。

## 回滚

- 关闭 BREAKOUT_RADAR_ENABLED 即停止新扫描；旧 completed 数据保留只读。
- 不删除或覆盖旧 API 字段。
- 不用 git reset、git clean 或大范围回滚处理部署故障。
- 数据库故障与应用核心故障分开处置，/ready 不绑定突破状态。
- 回滚不得把 unavailable 扫描改写成 completed；API 应继续读取回滚前最后一个
  completed 快照。

## 数据兼容限制

- 第一版 canonical universe 是项目固定主题池，不代表完整全美横截面。
- 外部候选缺少可靠行业时 sector percentile 为 null。
- 六态 market shape 已接入；旧 completed 快照中的 market_fit 仍可能为 null，
  新扫描在核心行情不足时也保持 null。
- 历史盘前数据缺失时 premarket_rvol 为 null。
