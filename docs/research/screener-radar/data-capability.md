# 数据能力表

审计时间：2026-09-13。研究 VM：4 vCPU / 16GB RAM / 约 247GB 可用磁盘。**不是**生产 16 核 / 32GB / 512GB。

密钥：进程环境中无 `MASSIVE_API_KEY` / `FINNHUB_API_KEY` / `FMP_API_KEY` / `MARKETDATA_TOKEN`。Massive MCP 当前不可用。未购买任何数据，未扩大付费预算。

生产库：`/data/optix.db` 与 `~/optix-data` 均不存在。不要假设历史扫描已挂载。

## 能力矩阵

| 数据 | 授权 | 历史覆盖 | 分辨率 | 更新语义 | 复权 | 退市 | 能支持的结论 | 不能支持的结论 |
|------|------|----------|--------|----------|------|------|--------------|----------------|
| Yahoo 日线 OHLCV | 生产已用公开兜底，免费有界下载 | 计划 2018-01 至今；以实际缓存为准 | 日 | 会话日；收盘后可见 | 同时保留未复权 OHLC 与 Adj Close | 当前主题池幸存者为主 | Grade B/C 排序与日线平台重建 | 当年实盘发布、退市修正收益 |
| Yahoo 分钟/5 分钟 | 公开接口存在 | 通常仅约 7–60 日 | 5 分钟 | bar_end | 视下载参数 | 差 | 仅能做近期盘中程序测试 | ORB / 盘中先止盈后止损 |
| 盘前盘后 | 生产实时通道默认关 | 历史盘前几乎不可得 | — | — | — | — | 无 | PREMARKET_GAP 全流程 |
| SPY/QQQ/行业 ETF | 同 Yahoo 日线 | 多数 2018+；XLC 约 2018-06 起 | 日 | 收盘 | 复权收益 | ETF 存续 | 基准与 market_fit 降级研究 | 完整 optional 风险组点时 |
| 公司行动 / 代码变更 | 仅 Yahoo 复权因子 | 不完整 | — | 事后因子 | 今日回算 | 不完整 | 披露口径 | 精确拆并股事件会计 |
| 当时股票池 / Discovery | 无 | 无 | — | — | — | 无 | 条件内排序 | 全市场发现、召回率 |
| 基本面 / 财报日历 | Finnhub/FMP 未配置 | 无点时版本 | — | 修订值 | — | — | 跳过（生产也不进内在分） | AI/基本面选股 |
| 期权特征 | 无密钥 | 无 | — | 实时 | — | — | 跳过（权重为 0） | 期权收益 |
| 已保存扫描快照 | 无生产库 | 无 | — | published_at | — | — | Grade A 不可用 | 当年页面可见信号 |
| LLM 新闻 | 无当时输出 | — | — | — | — | — | 禁止当作当时 AI 信号 | — |

## 本环境明确不可完整验证的雷达类型

- `OPENING_RANGE_BREAKOUT`
- `PREMARKET_GAP` / `GAP_HOLD` / `GAP_AND_GO` / `GAP_FADE`
- 盘中 RVOL_TOD
- 同一根 K 线同时触及止盈止损的路径
- 先止盈还是先止损

## 已完成的有界下载（2026-09-13）

- 路径：`/opt/cursor/research/screener-radar-data/yahoo-daily`
- 源：Yahoo/yfinance，`auto_adjust=False`（保留 Close + Adj Close）
- 主题池+基准：228/228 有至少一根有效日线
- 有效 K 线：479,397；丢弃 19,239（非正/非有限价格，含 ARM 上市前占位和 2026-05-25、2026-09-07 假期坏行）
- 内容哈希：`09be8d4a23ca1b4822b41798e1f06d2d6da2d1ae3eca6c1c56777fba8bcc9ec7`
- 最早共同起点并不存在：有的标的从 2018-01-02 起，有的晚至 2025-03-28（IPO/后加入主题池）
- SPY 开发区交易日：1008/1008
- 这是覆盖审计，不是收益结论；封存区收益未计算

## 下载策略

- 命令：`scripts/research/fetch_daily_ohlcv.py`
- 输出：`/opt/cursor/research/screener-radar-data/yahoo-daily/{manifest.json,bars.jsonl}`
- `trust=external_unverified`；SHA-256 只证明导入后文件未改。
- 回放命令禁止调用该下载器。

## 复权与过滤

生产选股 `_download_history(..., auto_adjust=True)`。原版基线因此用复权 OHLC 复现特征与 `min_price`。

这会把「今天回算后的绝对价格」用于历史低价过滤。该偏差在原版基线中保留并披露；候选试验 `candidate-unadjusted-min-price` 单独对照，不覆盖原版。
