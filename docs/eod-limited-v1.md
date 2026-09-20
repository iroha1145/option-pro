# 收盘技术（受限）启用、默认迁移与回滚

模式 ID：`eod_limited_v1`。选股系统默认是本模式；雷达默认仍是 `production`。本模式不是收益验证，只展示规则分数、观察与拒绝原因。

## 启用（目标实例）

1. 部署包含本 PR 的镜像。不要把 `research/` 或回传目录打进前端/生产镜像。
2. 新安装：管理员选股默认与代码默认均为 `eod_limited_v1`，周期缺省 `mid`。
3. 已有实例若只是初始化时把管理员默认写成了 `production`：启动后由幂等迁移 `screener_default_to_eod_limited_v1` 改成新默认。记录文件：`DATA_DIR/screener-default-to-eod-limited-v1.json`。
4. 用户打开 `/screener` 且选择为 `follow_default` / 未保存偏好时，页面消费新默认并回显中期。显式 `production` / `a0_mid_long` 个人选择不被覆盖。
5. 管理员若已固定 `a0_mid_long`，迁移跳过，不偷偷覆盖。
6. Owner 扫描/刷新与调度 `strength_refresh` 在新默认下走隔离批次 `DATA_DIR/eod-limited-v1/batch.json`，一次采集日线后预计算 9 组 profile/horizon。
7. 普通 GET 只读已发布快照，不向供应商取数。无快照时显示本模式不可用，可切回原版。

显式关闭自动迁移：环境变量 `OPTIX_SCREENER_DEFAULT_MIGRATION=0`。

## 回滚

1. 用户切回「原版排序」或「中长期趋势（试用）」。
2. 管理员把选股默认算法改回 `production`。
3. 撤销本次默认迁移（恢复迁移前的管理员默认，并阻止再次自动套用）：

```python
from app.services.runtime_settings import get_runtime_settings_store
from app.services.screener_default_migration import rollback_screener_default_migration

rollback_screener_default_migration(get_runtime_settings_store())
```

4. 需要停用入口时回退本 PR。删除 `DATA_DIR/eod-limited-v1/batch.json` 只影响新模式；原版 strength 24 变体缓存不受影响。
5. 删除迁移记录文件会让下一次启动再次把仍为 `production` 的初始化默认迁到新版；回滚后请保留该文件。

## 周期

- 省略 `timeframe` 或旧客户端带着 `all` 跟随新默认时，解析为 `mid`。
- 界面把已保存的「全部周期」回显为「中期」，并发送 `mid`。
- API 显式 `ranking_algorithm=eod_limited_v1&timeframe=all` 返回 `algorithm_view_conflict`。
- 原版 / A0 的 `all` 语义不变。

## 数据与标签

- 实时推断：`purpose=live_eod_inference`，日期为最近完整交易日。
- `2024-06-28` 只能标「历史示例」，不能当最新。
- 合成输入必须标 `SYNTHETIC`。历史种子面板必须覆盖目标交易日，并带可确认高低点，避免只有标签没有观察行。
- 默认不把 `volume_verified` / `dollar_liquidity_verified` 设为 true。
- Worker 日线走现有 `download_in_bounded_batches`，不要再传 `threads`。
- 隔离验证可用 `tickers=` 把当前主题名单收成有界子集；不在页面 GET 上传任意代码名单。
- 最近完整交易日由纽约日历收盘 + 半日/假期规则决定，不是固定日本时间。`2024-06-28` 若被 live 入口碰到会改标历史示例。
