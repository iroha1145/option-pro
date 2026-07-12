# 突破雷达研究验证

突破雷达的区间强势持续度（Range Persistence）继续保持影子模式（shadow）。
研究报告只能积累证据，不能修改运行配置，也不能自动开启正式权重。

## 数据边界

研究入口只读取状态为 `completed` 且已有 `published_at` 的扫描快照。事件与影子
记录按 `scan_run_id + event_id` 连接；同一个 `event_id +
range_persistence_version` 在连续扫描中只保留首次完成发布的观察，避免重复计数。

每条可标注观察必须通过点时一致性（Point-in-Time）检查：

```text
raw_as_of <= feature_cutoff_at <= event_at <= published_at
```

任一时间缺失、没有时区或次序错误，所有前瞻标签都标记为 `unavailable`。缺价格、
停牌造成的交易日缺口、历史尚未成熟等情况同样保持缺失，不补零，也不前向填充。

研究程序不联网抓行情。历史收盘价由独立的 JSON 文件输入，最小结构如下：

```json
{
  "schema_version": "breakout-forward-prices-v1",
  "dataset_id": "vendor-export-2026-07-12",
  "source": "licensed-market-data",
  "adjustment": "unadjusted",
  "timezone": "America/New_York",
  "calendar": "XNYS",
  "as_of": "2026-07-12T23:59:59Z",
  "prices": {
    "AAPL": [
      {"date": "2026-07-10", "close": 100.0},
      {"date": "2026-07-13", "close": 101.5}
    ]
  }
}
```

同一股票的日期必须严格递增，收盘价必须是有限正数，日期不能晚于 `as_of`。
加载器拒绝重复 JSON 键、`NaN`、无穷值、符号链接和超过 128 MiB 的文件，并在
报告中保存源文件的 SHA-256 内容哈希。该哈希只能证明导入后的文件未变化，不能
证明外部行情本身真实，因此输入信任状态固定记录为 `external_unverified`。

## 前瞻标签

当前默认提供 1、5、20、63 个交易时段的前瞻收益。其中 1 日保留事件标签观察，
5、20、63 日与区间强势持续度实验期限对齐：

```text
forward_return_h = future_close_h / event_price - 1
```

`event_price` 是事件快照中当时已知的价格；退出价只从 `trading_date` 之后的完整
日线收盘中按顺序选择。事件当日价格即使出现在行情文件中也不会被当作未来退出
价。周末和没有行情点的日期不计入时段数量。该入场值属于信号时点标记，不代表
发布时间之后能够成交的价格；没有后续盘中 K 线时，执行收益状态保持
`unavailable`。

这些标签是证券原始小数收益，不是相对市场基准的超额收益。现有快照没有与事件
时间严格对齐的基准入场价，程序不会用日线价格伪造。分组基准收益、交易成本、
滑点、最大有利波动、最大不利波动、退市补全和生存偏差控制仍为未验证项。

## 按日期滚动验证

滚动步进验证（Walk-Forward Validation）禁止随机划分。默认窗口为 60 个训练
日期、20 个验证日期和 20 个测试日期，按测试窗口长度继续向前滚动。同一交易日
的所有股票始终留在同一分区。

隔离期（Embargo）从验证和测试原始窗口开头移除指定数量的日期。随后执行清除
（Purge）：

```text
训练标签结束日 < 验证实际开始日
验证标签结束日 < 测试实际开始日
```

等于边界也会被移除。每个窗口保存原始行数、实际使用行数、清除行数、隔离行数、
起止日期、最大标签结束日和泄漏检查结果。训练、验证或测试样本不足时，窗口状态
为 `unavailable`，指标保持 `null`。

## 模型与消融

研究使用完全相同的成对样本比较：

- 模型 A：已保存的正式内在强势分 `production_score`。
- 模型 B：简单叠加区间强势持续度；历史快照没有保存这组点时分数，因此明确标记
  为 `unavailable`，不在研究时回算。
- 模型 C：已保存的同类趋势权重替换分 `hypothetical_score`。

验证集只用于在模型 A 与模型 C 之间选择，测试集只报告冻结选择后的结果。每个
期限分别输出每日秩相关系数（Rank IC）、信息系数比率（ICIR）、Top-K 相对当日
候选均值的超额收益和 Top-K 命中率。两组分数任一缺失时，该观察不会进入任一组，
避免样本口径不同。

报告包含研究配置哈希、研究版本、区间强势版本、强势评分版本、规范股票池版本、
价格版本、样本日期范围、标签覆盖率、缺失原因和每个滚动窗口的审计信息。即使有
可计算窗口，`decision_status` 仍是 `insufficient_for_production_decision`，
`production_mode_recommendation` 仍是 `shadow`。

## 只读导出

```bash
cd backend
python -m app.services.breakouts.research \
  --db /data/optix.db \
  --validation-prices /tmp/breakout-forward-prices.json \
  --validation-out /tmp/breakout-walk-forward.json \
  --validation-horizons 1,5,20,63 \
  --validation-train-dates 60 \
  --validation-dates 20 \
  --validation-test-dates 20 \
  --validation-step-dates 20 \
  --validation-embargo-dates 1 \
  --validation-minimum-rows 10 \
  --validation-top-k 5
```

数据库以 SQLite 只读查询模式打开，结果使用临时文件同步落盘后原子替换。输出
路径不得覆盖数据库、数据库旁路文件或历史价格输入。该能力只提供离线研究命令，
没有对外开放上传行情文件的 HTTP 接口。

专项测试：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend \
  python3 -m pytest -q \
  tests/test_breakout_research.py \
  tests/test_breakout_research_validation.py
```
