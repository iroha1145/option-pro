# 收盘技术（受限）启用与回滚

模式 ID：`eod_limited_v1`。默认算法仍是 `production`。本模式不是收益验证，只展示规则分数、观察与拒绝原因。

## 启用（测试实例）

1. 部署包含本 PR 的镜像。不要把 `research/` 或回传目录打进前端/生产镜像。
2. 管理员在催化管理面板把「选股默认算法」保持为原版，或仅在测试实例设为「收盘技术（受限）」。
3. 用户在选股页主动选择「收盘技术（受限）」。首次遇到周期=全部时，界面改到中期并按 mid 请求。
4. Owner 在该模式下点扫描/刷新：提交现有 `strength_refresh`，worker 走隔离批次 `DATA_DIR/eod-limited-v1/batch.json`，一次采集日线后预计算 9 组 profile/horizon。
5. 普通 GET 只读已发布快照，不向供应商取数。无快照时显示本模式不可用，可切回原版。

## 回滚

1. 用户切回「原版排序」或「跟随默认」。
2. 管理员把选股默认算法改回 `production`。
3. 需要停用入口时回退本 PR，或保持代码但不要把管理员默认设成 `eod_limited_v1`。
4. 删除 `DATA_DIR/eod-limited-v1/batch.json` 只影响新模式；原版 strength 24 变体缓存不受影响。

## 数据与标签

- 实时推断：`purpose=live_eod_inference`，日期为最近完整交易日。
- `2024-06-28` 只能标「历史示例」，不能当最新。
- 合成输入必须标 `SYNTHETIC`。历史种子面板必须覆盖目标交易日，并带可确认高低点，避免只有标签没有观察行。
- 默认不把 `volume_verified` / `dollar_liquidity_verified` 设为 true。
- Worker 日线走现有 `download_in_bounded_batches`，不要再传 `threads`。
- 隔离验证可用 `tickers=` 把当前主题名单收成有界子集；不在页面 GET 上传任意代码名单。
- 最近完整交易日由纽约日历收盘 + 半日/假期规则决定，不是固定日本时间。`2024-06-28` 若被 live 入口碰到会改标历史示例。
