# option-pro 美股收盘选股研究 v1

研究轨道，不是生产默认，也不是已经回测胜出的策略。

- 配置：`config/registry.json`、`experiment_manifest.json`（864）、`etf_subasset_manifest.json`（180）、`composite_manifest.json`（12）
- 参考评分器：`reference/registry.py`（不联网、不跑市场回测）
- 引擎：`backend/app/services/research_eod_v1/`
- 只读 shadow API：`/api/research/eod/v1/*`，默认 `RESEARCH_EOD_V1_ENABLED=false`
- 回传包：`return_pack/`

没有许可的十年 PIT 数据时，市场试验状态必须是 `DATA_INSUFFICIENT` / `INSUFFICIENT_PIT_HISTORY`。不得把工程测试写成收益验证。
