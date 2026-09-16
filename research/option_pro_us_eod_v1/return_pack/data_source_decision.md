# 数据源决策

核对日：2026-09-16。研究入口与生产行情供应商分离。生产 Massive/Yahoo 链路未改。

## 路线

1. **LocalParquetProvider**：`RESEARCH_EOD_LOCAL_DATA` 或 `research/option_pro_us_eod_v1/data/local`。本环境无授权离线文件。`probe_status=ACCESS_TESTED`，`daily_bars=empty`。
2. **YahooDiagnosticProvider**（路线 A）：`yfinance==1.5.1`，参数 `interval=1d, auto_adjust=False, actions=True, repair=False, keepna=True, prepost=False, threads=False`。样本探测 5/5 成功（2020-01-02–2020-03-02，各 40 根）。随后批量下载当前 24 主题 214 只不重复代码，区间 2015-01-02–2026-09-17（end 不含），**214/214 有日线**，末日均为 2026-09-16。日线已写入 `return_pack/yahoo_current_universe/daily_bars.parquet`（582198 行）。
3. **MassiveEnvProvider**（仅环境变量回退）：本轮 Yahoo 已通，**没有**读取或使用任何 Massive 密钥，也没有改生产 `MASSIVE_API_KEY` 配置。`probe_status=AUTH_REQUIRED`（探测时环境未配置）。

未使用 Sharadar / EODHD / Norgate。未购买套餐。未把授权全量行情提交到 GitHub。

## 明确限制

- 当前生产 `SECTORS` 名单回放 ≠ 十年全市场，≠ 无幸存者偏差。
- `auto_adjust=False` 不证明 Close 是历史未复权成交价。不得用其做历史 $5 门槛或真实开盘账本。
- `Adj Close` 只作 TRI 代理，不是买卖价。
- `prepost=False` 不是常规时段成交量证明。`volume_session_scope=UNKNOWN`。
- 无历史分类 / 退市并集 / 公司行动期刊。分类为 `CLASSIFICATION_CURRENT`。
- 下载时刻 ≠ 当年可知时点。`vintage_status=download_time_not_pit`。

## 能力级别

| 源 | 级别 | 说明 |
| --- | --- | --- |
| Yahoo 日线 | ACCESS_TESTED + 当前池 SAMPLE_VERIFIED | 214 只当前代码有条数；未做分钟核验，未称 BROADLY_AUDITED |
| Yahoo 公司行动 | DOCUMENTED_ONLY / partial | 接口存在，本轮未作为执行账本 |
| Local | ACCESS_TESTED | 目录不存在有效文件 |
| Massive 研究回退 | DOCUMENTED_ONLY | 本轮未调用 |
| Sharadar / EODHD | DOCUMENTED_ONLY | 无授权 |

## 与生产源

`SOURCE_PARITY_PENDING`。对照脚本未跑重叠窗口。这是部署阻塞，不是本轮工程阻塞。
