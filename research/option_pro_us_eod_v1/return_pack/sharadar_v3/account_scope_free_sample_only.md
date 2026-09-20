# Sharadar 账户范围：FREE_SAMPLE_ONLY

负责人已确认：本账户只使用免费 Sample，未购买任何套餐。

`FREE_SAMPLE_ONLY` 是账户范围标记（`kind=account_scope`），不是供应商 HTTP / 错误码，也不改写已记录的 401 / 403 / 400。

## 现在停止的事

- 不要把长历史空页、全市场缺口、bulk `403 Forbidden` 当成已购权限异常再排查。
- 不要重复已经失败的覆盖探针。
- 不要要求更换 agent 来“补覆盖”。
- 不要自动购买。
- 不要恢复旧 214 小池权重搜索。
- 不要降低十年以上正式验证要求。

## 保留的事

- 现有适配器、身份绑定、401/403 分列、schema `format=json` 映射、Massive 未复权对账标签等工程修复都保留。
- 已落盘的脱敏 HTTP 与哈希不倒写。
- `full_backfill_started=false`。正式数据阶段暂停，等数据来源与预算确定后再恢复。

详见同目录 `entitlement_and_history_index.md`。
