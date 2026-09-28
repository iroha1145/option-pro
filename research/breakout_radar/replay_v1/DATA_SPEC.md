# 突破雷达历史回放：数据规格（第一阶段 a，2026-09-28）

本文回答「用什么数据、取多少、怎么代理」。所有结论都从代码读出，标注文件与行号；文档目录 `docs/breakout-radar/` 只作参照，不作依据。行号以工作树 `claude/radar-replay-v1`（自 `origin/main` 35ab1395 建出）为准。

回放的目标：用生产代码逐次扫描地重演突破雷达（Breakout Radar），只有「发现」这一段用代理数据，其余全部调用生产函数。评估口径沿用全市场选股 v1.6、v1.7 的研究包（`research/option_pro_us_eod_v1/return_pack/full_market_v1_6/scripts/evaluate.py`）。

## 0. 取数规模与代理决定

### 0.1 取数规模（5 分钟 K 线）

单位：一个「代码 × 交易日」的 5 分钟 K 线（含盘前盘后，04:00 到 20:00 美东，最多 192 根）叫一个「票日」。协调方实测一个票日的响应约 20 KB。

每个交易日 D 需要的票日 = S(D) + C(D) + L(D) × k × (1 − o)。

- S(D)：发现阶段的「候选超集」。用日线算出的必要条件筛出来（见 1.4）。这是当天需要 K 线的股票数。
- C(D)：前一日遗留、还没终结的事件所属股票（生产叫 carryover），且不在 S(D) 里的部分。它的上界是 L(D−1)。
- L(D)：当天进入盘中细化阶段（每次扫描最多 30 只）的股票并集。它们需要同时点相对量的回看历史。
- k：回看交易日数。生产用 20 个完整交易日（`feature_engine.py:355-360`），实际抓 30 个日历日（`adapters/price_data.py:174-175`），约 21 个交易日。
- o：回看票日里已经因为别的原因抓过的比例（同一只股票连续几天在超集里，回看窗口大量重叠）。

三个窗口的交易日数（用 `app.services.market_calendar` 实算）：

| 窗口 | 日线冻结包 | 5 分钟 K 线可取 | 交易日数 N |
|---|---|---|---|
| (a) 五年 | 2021-09-28 到 2026-09-25 | 2021-10-04 起（协调方实测 2021-09-24 被拒） | 1,250 |
| (b) 三年 | 2023-09-28 到 2026-09-25 | 同 | 751 |
| (c) 两年 | 2024-09-27 到 2026-09-25 | 同 | 500 |

五年窗口的左边界每天前移一天。抓取花几天，最早的几天就丢几天。选 (a) 时按「从最早的日期开始抓」执行，有效起点以抓完时为准。

S 和 L 我在本机算不出来（冻结包和 `replay.sqlite` 都在 Colab 与 Google Drive 上）。下面的数字是**示意**，用 S = 650、C = 50、L = 100、k = 20、o = 0.5 代入。真值请协调方用附录 A 的普查查询在 `replay.sqlite` 上算，几分钟就有结果。

| 窗口 | 票日数 | 字节 | 只抓当天（第一遍，见 6.2） |
|---|---|---|---|
| (a) 五年，N = 1,250 | 约 2.1 百万 | 约 42 GB | 0.88 百万票日，约 17.5 GB |
| (b) 三年，N = 751 | 约 1.3 百万 | 约 26 GB | 0.53 百万票日，约 10.5 GB |
| (c) 两年，N = 500 | 约 0.85 百万 | 约 17 GB | 0.35 百万票日，约 7 GB |

请求数取决于合并方式。一个票日一个请求时，请求数等于票日数；按 20 请求每秒，(a) 约 30 小时，(b) 约 18 小时，(c) 约 12 小时。生产客户端的接口能一次请求一段日期：`ticker_range` 的 `limit=50000` 数的是基础分钟根数，不是返回的 5 分钟根数（`massive.py:267-285` 的注释），一页覆盖 50,000 ÷ 960 ≈ 52 个含盘前盘后的交易日。把同一只股票连续需要的日期合成一段，请求数按 ⌈天数 ÷ 52⌉ 算，比一日一请求少一个量级；字节数不变。20 请求每秒是对 20 KB 响应量的实测，多日大响应是受请求数还是受带宽限制，目前不知道，建议先抓 20 个多日请求测一下。

能大幅削减取数的设计（标明是否改变算法）：

1. **日线必要条件做候选超集**（不改算法）。全市场约 1 万只股票和基金，每天只有满足「最高价 ≥ 前收 × 1.03 且成交量 ≥ 前 10 日均量 × 1.5 且最高价 ≥ 2 美元」的那部分才可能在任一扫描时点通过 TradingView 的过滤条件（推导见 1.4；实际预筛用 1.025 与 1.4 倍留余量）。这一条把每天 1 万只减到几百只。
2. **按股票合并请求**（不改算法）。上面已说。
3. **回看只抓真正进入盘中阶段的股票**（不改算法）。发现和日线两段只用日线和当天 K 线，先跑一遍就知道每天哪 30 只进了盘中阶段，再只给它们抓回看（见 6.2 的两遍法）。
4. **回看缩到 10 个交易日**（改算法）。字节约减一半。相对量的分母从 20 日中位数变成 10 日中位数，会改变确认与打分。不作基线，可作候选。
5. **用日成交量曲线代替分钟回看**（改算法）。相对量分母改为「20 日均量 × 市场级日内成交量分布 f(t)」，回看完全不用抓。这是另一套算法，可作候选测试，不能作基线。

规模最大的风险在相对量的定义（1.3 第 5 项）。如果 TradingView 的 `relative_volume_10d_calc` 是「同时点」口径，成交量那条必要条件就不成立，超集只能用涨幅这一条，S 可能翻两三倍。这件事在烟雾测试里用生产库的候选表校准（7.2）。

### 0.2 代理决定

采用**忠实重算**：用 Massive 日线加 5 分钟 K 线，在每个扫描时点重算 TradingView 的三条过滤条件、按涨幅排序、取前 150，然后走生产的规范化（`normalizer.py`）。不换漏斗。

理由：
- 三个上限（150、60、30）之间不只是截断。进入日线阶段的 60 只是按涨幅排序取的（`service.py:1933-1937`），进入盘中阶段的 30 只按（基底质量，涨幅）排序取（`service.py:2315-2323`）。换一种发现口径，进漏斗的是另一批股票，后面每一段都跟着变，就没法再和生产比对。
- 生产库保存了最近 90 天每次扫描的 TradingView 候选表（`breakout_candidates.candidate_json`，含涨幅、相对量、价格、行业、资产类型），所以「重算得像不像」是可以量的（7.2）。

两处不得不偏离，都写明：
- **市值过滤取消**。TradingView 的 `market_cap_basic` 没有点时（point-in-time）替代品（1.3 第 6 项）。生产在市值小于 2 亿美元时剔除候选（`normalizer.py:187-192`）；回放不剔。流动性上还有 20 日平均成交额 ≥ 1,000 万美元这道硬门（`service.py:2085-2092`），会挡掉大部分微型股。差额无法从生产库量出来（被剔除的行不落库），建议协调方对 TradingView 做一次实时探测，数一下满足涨幅、相对量、价格三条件的行里市值小于 2 亿的比例。
- **行业改用 SIC 表**。TradingView 的 `sector` 字符串没有替代来源。回放用 v1.7 冻结的 `ticker_sic.json.gz`（9,729 组代码与 CIK，股票覆盖约 78%）把 SIC 映到 11 只行业 ETF；映射表见附录 C。基金和无 SIC 的股票走生产的主题兜底（`adapters/universe.py:121-137`）。行业只影响 `sector_fit_score`（告警优先级权重 0.10，`scoring.py:222-228`）和相对强弱里的行业项。

其余发现字段都能重算，见 1.3。

## 1. 发现阶段

### 1.1 生产查询原文

提供方（Provider）只有 TradingView 一家（`config.py:34-37`）。请求是 `POST https://scanner.tradingview.com/america/scan`（`providers/tradingview.py:37`，`:283-289`），请求体由 `_payload` 生成（`tradingview.py:140-200`）。

盘中（`REGULAR_MOVERS`，`clock.py:142-145` 选定）：

```json
{"filter": [
   {"left": "close", "operation": "egreater", "right": 2.0},
   {"left": "change", "operation": "egreater", "right": 3.0},
   {"left": "relative_volume_10d_calc", "operation": "egreater", "right": 1.5}],
 "options": {"lang": "en"}, "markets": ["america"],
 "symbols": {"query": {"types": []}, "tickers": []},
 "columns": ["name","exchange","description","type","typespecs","close","change",
             "volume","relative_volume_10d_calc","market_cap_basic","sector"],
 "sort": {"sortBy": "change", "sortOrder": "desc"},
 "range": [0, 150]}
```

阈值来自设置：`min_price` 2.0（`config.py:133`）、`regular_min_change_pct` 3.0（`:137-139`）、`regular_min_relative_volume` 1.5（`:140-142`）、`provider_result_limit` 150（`:71-73`）。

盘前（`PREMARKET_GAPPERS`）：过滤 `premarket_close ≥ 2.0`、`premarket_change ≥ 5.0`（`premarket_min_change_pct`，`config.py:146-148`）、`premarket_volume > 0`；按 `premarket_change` 降序；列多出 `premarket_close`、`premarket_change`、`premarket_volume`（`tradingview.py:52-66`，`:151-161`）。

还有一个「成交额领先」档（`REGULAR_DOLLAR_VOLUME_LEADERS`，前 100），Worker 从不选它（`clock.py:142-145`），回放不做。

### 1.2 查询之后的生产处理

1. 逐行规范化（`normalizer.py:60-157`）：价格、涨幅、成交量缺一不可；资产类型由 `type` 与 `typespecs` 判定（`:36-57`）。
2. 过滤去重（`normalizer.py:160-224`）：剔除杠杆基金（`asset_policy.py:20-55`，靠名称里的「2x」「Ultra」等）；只留普通股、ADR、ETF（`allow_etf` 默认开，`config.py:85`）；价格 ≥ 2；**市值存在且 < 2 亿时剔除**；再核对一次涨幅 ≥ 3 与相对量 ≥ 1.5；同一代码留涨幅大的；按涨幅降序取前 150。
3. 服务层（`service.py:1829-1838`）再剔一次杠杆基金并截到 150。
4. 日线阶段取前 60，排除当天已有盘前缺口事件的代码（`service.py:1895-1937`）；20 日平均成交额 ≥ 1,000 万美元（`:2085-2092`）。
5. 检测基底、算区间持续度，按（基底质量降序，涨幅降序，代码）排序取前 30 进盘中阶段（`:2260-2323`）。
6. 盘中阶段：当天累计成交额硬门，盘中 ≥ 500 万美元，盘前 ≥ 50 万美元（`:2395-2428`，阈值 `config.py:143-151`）。

### 1.3 每个字段能不能从 Massive 重算

「当时」指扫描时点 as_of；「最后一根完整 K 线」指结束时间 ≤ as_of 的最后一根 5 分钟 K 线（`feature_engine.py:134-156`）。

| 字段 | 能否重算 | 回放的取法 | 说明 |
|---|---|---|---|
| `name`、`exchange`、`description` | 能 | 每周点时目录（`/v3/reference/tickers?date=`）的 `ticker`、`primary_exchange`、`name` | 只用于展示，不进逻辑。交易所代码写法不同（XNAS 对 NASDAQ）。 |
| `type`、`typespecs` | 能，需映射 | 目录的 `type` 码映到生产的资产类型（附录 B）；杠杆靠生产的名称正则 | TradingView 的 `typespecs` 可能带 `leveraged` 标记，Massive 没有；少数名称里没写倍数的杠杆基金会漏剔。 |
| `close`（最新价） | 能，差一根 K 线 | 最后一根完整 5 分钟 K 线的收盘 | TradingView 用最新成交价，含未完成的那根 K 线。回放晚最多 5 分钟。 |
| `change`（涨幅） | 能 | （最新价 ÷ 前一交易日收盘 − 1）× 100；前收取 D−1 日线，若 D 当天有拆股，前收乘以 split_from ÷ split_to | 同上差一根 K 线。 |
| `volume` | 能 | 当天从 04:00 起到最后一根完整 K 线的累计成交量 | 只进 `provider_volume`，不进任何分数。 |
| `relative_volume_10d_calc` | 能，但定义要校准 | 回放定义：当天累计成交量 ÷ 前 10 个交易日全日成交量均值 | TradingView 的公式在代码里看不到。若它其实是「同时点」口径，见 7.2 的校准与 0.1 的风险。 |
| `market_cap_basic` | 不能 | 取消这道过滤；`market_cap_quality` 记为缺失 | 需要点时流通股数。`/v3/reference/tickers` 列表接口不带股数；详情接口只给当前值。缺失时 `weighted_score` 把权重重新归一（`scoring.py:24-86`），流动性分里少 0.10 的成分，对告警优先级影响约 0.35 × 0.10 × 0.10。请协调方顺带确认冻结的 `ticker_sic.json.gz` 里是否保留了详情接口的市值或股数字段。 |
| `sector` | 不能 | SIC 表映到行业 ETF（附录 C） | 见 0.2。 |
| `premarket_close`、`premarket_change`、`premarket_volume` | 能 | 04:00 到 09:30 的 5 分钟 K 线：最后一根完整 K 线收盘、相对 D−1 收盘的涨幅、累计量 | 与生产一样只用真实盘前数据。 |

### 1.4 候选超集：为什么只抓几百只

盘中扫描时点 t 的涨幅 change(t) 用最后一根完整 K 线的收盘算，它不会超过当天日线最高价对前收的涨幅。累计成交量 cumvol(t) 不会超过当天全日成交量。最新价 ≥ 2 要求日线最高价 ≥ 2。所以在回放自己定义的相对量口径下，下面三条是股票在 D 日任一扫描时点通过过滤的**必要条件**：

- high(D) ≥ 1.03 × close(D−1)（D 日有拆股时前收按比例调整）；
- volume(D) ≥ 1.5 × mean(volume(D−10 … D−1))；
- high(D) ≥ 2。

这是取数优化，不是提前用未来信息做决定：决定本身仍只用 t 之前的 K 线，被超集排除的股票在 t 时点本来也过不了。

实际筛选要留余量：汇总日线的最高价、成交量与 5 分钟 K 线的合并口径未必完全一致，一根 5 分钟收盘略高于日线最高价的情况会漏过严格的 1.03 门槛，而且漏了就永远不会被抓。所以预筛用 1.025 与 1.4 倍，普查同时给出严格值与放宽值两组数量。

一个前提要协调方核实：Massive 汇总日线的 `h` 是否包含盘前盘后。若包含，盘前扫描的超集也是精确的（盘前涨幅 ≥ 5% 蕴含 high(D) ≥ 1.05 × close(D−1)）。若只含正常时段，盘前超集只能用启发式：open(D) ≥ 1.02 × close(D−1) 或 high(D) ≥ 1.05 × close(D−1)，漏掉的比例在烟雾测试里用生产的盘前候选行量出来。核实方法：抽 50 个票日，比较 `h` 与 04:00 到 20:00 的 5 分钟最高价、09:30 到 16:00 的 5 分钟最高价。

## 2. 扫描节奏

- 节奏来自 `config/personal.toml` 的 `[breakout]`：盘中 300 秒，盘前 600 秒，休市 1,800 秒；`config.py:96-113` 读入。
- 时段判定（`clock.py:79-87`）：04:00 到 09:30 盘前；09:30 到收盘（16:00，提前收盘日 13:00）盘中；收盘到 20:00 盘后；其余休市。假日规则在 `market_calendar.py`。
- Worker 在盘后与休市**不扫描**，只做 T1 收盘补算（`worker.py:834-861`），所以没有盘后扫描。
- 调度用单调时钟按间隔累加，每轮加 0 到 5%（最多 30 秒）的随机抖动（`worker.py:1142-1165`）；连续降级时间隔翻倍，最多 8 倍（`:1146-1159`）。所以生产的扫描时刻不落在整分钟格点上，也不完全等距。
- 每日约 32 次盘前扫描（5.5 小时 ÷ 约 615 秒）加约 76 次盘中扫描（6.5 小时 ÷ 约 307 秒）。窗口内有 10 个提前收盘日。

回放的时点网格（定死，写进预登记）：
- 盘前：04:10、04:20、…、09:20（32 次）。
- 盘中：09:35、09:40、…、15:55（77 次）；提前收盘日到 12:55（41 次）。
- 每次扫描看到的 K 线是结束时间 ≤ 扫描时点的那些（`feature_engine.py:141-146`，等号包含）。09:35 看到 09:30 那根。09:30 到 09:34 之间的扫描看不到任何完整 K 线，候选会因为累计成交额缺失被过滤掉（`service.py:2424-2428`），所以不设 09:30 这一点等价。
- 开盘区间 30 分钟（`config.py:164-166`），需要 09:30 到 09:55 六根完整且时点 ≥ 10:00（`feature_engine.py:531-548`），所以最早在 10:05 那次扫描能触发开盘区间突破。
- 每个正常交易日 109 次扫描；生产用 `MarketClockSnapshot` 决定时段，回放注入同一时钟（`worker.py:829-834` 接受外部快照）。
- 收盘后另加一轮：16:30（提前收盘日 13:30）。这一轮不扫描，只跑 T1 收盘补算，因为 `_complete_pending_t1` 只在盘后与休市的周期里执行（`worker.py:834-836`）。没有它，基线 B1 的 T1 状态永远停在 pending。

要用数据核实的两件事（生产库读出来）：`breakout_scan_runs.scheduled_at` 的间距分布；`config_hash`（`worker.py:186-189`，等于设置 `model_dump(mode="json")` 的 SHA-256）与回放设置的哈希是否一致。不一致说明生产有环境变量覆盖。

## 3. 分钟历史回看

一个候选在一天里，盘中阶段要的 5 分钟 K 线：

- 当天：所有特征（VWAP、开盘区间、持有根数、累计成交额、缺口证据）只用当天（`feature_engine.py:452-585`；`service.py:535-694`）。
- 回看：同时点相对量 `compute_time_of_day_rvol` 取当天之前最近 20 个有完整正常时段 K 线的交易日，至少 5 个（`feature_engine.py:355-449`）。生产的 Massive 通道一次抓 30 个日历日（`adapters/price_data.py:173-188`），落到约 20 到 21 个交易日；Yahoo 兜底抓 20 天（`:229-235`）。
- 遗留事件的续扫也算同一份特征（`service.py:904-946`），所以它们同样要回看。

倍数：每个进入盘中阶段的票日，需要约 21 个票日的 K 线（当天加 20 个回看日）。回看日只需要 09:30 到收盘的成交量，但接口按日返回全天，无法只取正常时段（除非用毫秒起止时间，那又退回一日一请求）。

相对量的结果里有个 `quality` 字段（样本数 ÷ 20），打分不用它，只用比值本身（`service.py:484`）。回看少于 20 天不会让特征失效，只是分母的中位数换了样本。

## 4. 日线充实的输入

先回答标题里的问题：**「强势」不读选股页的快照。** 它由 `ExistingStrengthAdapter.score_from_daily_snapshots` 用雷达自己抓的日线现场重算（`adapters/strength.py:67-116`，调 `scanner.score_ticker_frames`，`strength/scanner.py:2366-2406`，实现在 `:2055-2176`）。输入只有候选与 SPY 的日线帧和 as_of。所以选股回放每 5 个交易日一份的记录用不上，也不需要。

各项来源与回放取法：

| 输入 | 生产来源 | 回放来源 | 备注 |
|---|---|---|---|
| 候选、SPY、行业 ETF 的日线 | `price_data.daily(symbols, period="2y")`（`service.py:2017-2028`）；`YahooPriceDataAdapter.daily` 走 `scanner._download_history`（`adapters/price_data.py:115`）；Massive 为主源，`ticker_range(…, 1, "day", adjusted=True)` 取 770 个日历日（`scanner.py:620-682`，`:527-540`），要求 ≥ 380 根且最新一根不早于 7 天，否则整只回落 Yahoo（`:542-606`） | `replay.sqlite` 的原始日线加拆股表，本地做拆股复权，只应用执行日 ≤ D 的拆股（`eod_limited/market_data.py:598-669` 的同一算法） | Massive 的 `adjusted=true` 只做拆股复权，与本地复权等价，差别在浮点。上市不足 380 天的股票生产用 Yahoo，数值同源同口径，回放一律用本地。 |
| 日线截止 | `trim_daily_bars` 按最后完整交易日裁（`feature_engine.py:58-83`）；盘前与盘中扫描的完整日是 D−1，收盘后是 D | 回放的行情适配器只喂到 `completed_daily_session(cutoff)` 那一天为止，这是生产自己的裁剪规则：盘中扫描得到 D−1，16:30 的 T1 补算得到 D | 生产抓下来的帧可能含 D 日未完的日线，所有用到的路径都裁掉它，回放直接不喂更稳。 |
| 行业 ETF 映射 | `ThemeCanonicalUniverseAdapter.sector_benchmark(ticker, provider_sector)`（`adapters/universe.py:121-137`）：先看 TradingView 行业字符串，再看主题唯一归属 | 候选的 `sector` 字段填附录 C 映出的字符串，让生产映射表直接命中 | 见 0.2。 |
| 强势分 | 上面已说 | 直接调生产适配器，不改 | `_feature_row` 要 ≥ 63 根日线（`strength/features.py:79-84`），52 周高点要 252 根。 |
| 平台基底 | `detect_base(ticker, daily, cutoff)`（`base_detector.py:217-250`），只吃日线，窗口 10 到 80 天 | 直接调 | 与选股引擎的 B 家族不是同一套代码。 |
| 相对强弱 | `relative_strength_features(股票, SPY, 行业 ETF, 行业广度)`（`relative_strength.py:50-118`） | 直接调 | 5、20、63 日超额。 |
| 大盘形态 | `ExistingMarketShapeAdapter` 调 `scanner.market_strength(as_of)`（`adapters/market_shape.py:15-22`），后者抓 23 只基准两年日线后调 `compute_market_regime(index_data, as_of)`（`scanner.py:2444-2472`），滞回状态机在函数内部按日重放（`market_regime.py:816-868`） | 写一个回放版适配器：用 `replay.sqlite` 里的基准日线帧调生产的 `compute_market_regime`，再按生产适配器的方式装成 `MarketShapeSnapshot` | 基准名单 `MARKET_BENCHMARKS`（`market_regime.py:23-27`）：SPY、QQQ、IWM、RSP、HYG、IEF、TLT、GLD、11 只行业 ETF、SOXX、SMH 都在汇总日线里；**^VIX 与 ^TNX 是指数，不在**。两者只进可选的「风险偏好」组（`market_shape.py:206-219`；`market_regime.py:286-335`）。缺了不会「不可用」，会变「降级」，置信度收缩，市场适配分向 50 收拢（`market_shape.py:681-685`，`:733-760`）。要忠实就另抓两条日序列：VIX 用 Massive 指数接口 `I:VIX`（`massive.py:31-39` 的映射），^TNX 是十年期收益率 × 10 的指数，代码取的是 20 日水平差（`market_regime.py:301`），若用 FRED 的 DGS10 要乘 10。 |
| 滞回参数 | `MarketShapeHysteresisConfig.from_env()`：进入确认 2 天、退出确认 2 天、最短停留 3 天、历史 20 天，可被 `MARKET_SHAPE_*` 环境变量覆盖（`market_shape.py:35-59`） | 用生产实际值 | 请协调方导出生产 Worker 容器的 `MARKET_SHAPE_*`、`BREAKOUT_*`、`RANGE_PERSISTENCE_*` 环境变量。 |
| 区间持续度与流动性分布 | 规范股票池 214 只（`sectors.py`，24 个主题）的日线，每个完整交易日算一次（`service.py:1978-2258`） | 直接调，日线来自 `replay.sqlite` | 股票池是 2026 年的名单（含 CRWV 这类新股），历史上会有缺失，覆盖率低于 60% 时分布标记降级。影子模式下持续度不进正式分（`service.py:2722-2726`，`personal.toml` 的 `range_persistence_mode = "shadow"`）。 |
| T1 | `attach_t1_features(event, daily)`（`t1_priority.py:620-670`），收盘后由 `_complete_pending_t1` 补算（`worker.py:469-827`） | 直接调，用 D 日收盘后的日线 | 只用日线。T1 排序视图只在 16:00 之后才有，评估它必须以 D+1 开盘入场，否则是前视。 |

**成本实测**（本机单核，合成的两年日线帧与 21 天 × 192 根的分钟帧，用工作树代码）：

| 函数 | 每只耗时 |
|---|---|
| `detect_base` | 约 24 毫秒 |
| `_score_ticker_frames_sync`（强势分，含持续度） | 约 44 毫秒 |
| `compute_range_persistence` | 约 43 毫秒 |
| `compute_feature_snapshot`（盘中特征，含 20 日回看的相对量） | 约 43 毫秒 |

哪些能按天缓存、哪些不能，取决于代码在谁手里：
- 回放注入的适配器可以缓存：行情帧、强势分（结果只依赖最后完整交易日，一天内各次扫描相同）、大盘形态。
- 服务自己的计算不能缓存，除非改生产代码：`detect_base` 对每次扫描的每个日线阶段候选都跑（`service.py:2265`）；影子模式下的区间持续度也是（`:2282-2304`）。规范股票池的分布服务自己已按完整交易日缓存（`:313-367`，状态 active 时不过期）。

每次扫描的估算：日线阶段最多 60 只 × （24 + 43）毫秒 ≈ 4 秒；盘中特征给最多 30 只新候选加遗留事件，遗留事件每次最多 150 条（5.1），按 43 毫秒一只算是 1.3 到 7.7 秒；再加状态机与打分。合计每次扫描约 5 到 15 秒，一天 109 次约 9 到 27 分钟。五年 1,250 天约 190 到 570 核时；变数是遗留事件的数量，烟雾测试的两周会给出实数。天与天之间靠 24 小时的遗留事件相连，不能完全并行；可按段并行（例如 40 段，每段前多跑 2 天预热，预热结果丢弃），单机 40 进程约 5 到 15 小时。

一个省一半日线阶段算力的开关：`RANGE_PERSISTENCE_MODE=disabled` 会跳过每个候选的区间持续度计算（`service.py:2268-2281`）。正式分本来就不吃持续度（`:2692-2706` 传入的调整为 0，`:2722-2726` 在非 enabled 模式下取正式分），所以理论上分数不变。但强势分也接收这个模式（`:2110`），是否逐字节不变要在烟雾周上比对过才能用，这里不当作事实。

## 5. 状态、事件与展示

### 5.1 跨扫描、跨日保存的东西

全部在 SQLite 仓库里（`repository.py`）：

- 事件当前版（`breakout_events`，`:369-388`，身份 `event_id` 唯一），每次扫描的事件快照（`breakout_scan_events`，`:404-421`），状态转换（`breakout_transitions`，`:390-402`），基底（`breakout_structures`），候选（`breakout_candidates`），提供方快照，影子研究行。
- 事件身份：`event_id = sha256(trading_date | ticker | setup_type | pivot_id)[:32]`（`lifecycle.py:222-237`）。`pivot_id` 三种来源：基底哈希（代码、基底起止日、阻力区、检测器版本，`base_detector.py:151-161`）；开盘区间 `orb-{代码}-{日期}-{区间高点:.6f}`（`service.py:2729-2738`）；其他 `{setup}-{代码}-{日期}`（`:2742-2745`）。同一交易日同一基底只会有一个事件；开盘区间突破与日线基底突破可以并存，各有身份（`:2985-3005`）。
- 生命周期（`lifecycle.py:22-75`，`:163-200`）。一次扫描最多推进 4 步（`service.py:2843-2865`）：DISCOVERED 先无条件进 WATCHING；WATCHING 那一步把 confirmed 与 extended 强制为假，只能进 TRIGGERED；再一步才能进 CONFIRMED。所以新候选一次扫描内最远到 CONFIRMED。
- 遗留事件（carryover）：每次扫描从仓库读非终态事件（`load_carryover_events`，`repository.py:3991-4078`；`worker.py:229-236`），条件是最新完整快照的 first_seen_at、last_seen_at 与 published_at 都 ≤ as_of，最多 150 条，最久未复核的优先；超过 150 条时 `has_more` 置真，剩下的等下次。它们的代码不再从发现通道建新事件（`service.py:2357-2360`，`:2866-2868`，`:2982-2984`）；当天已有盘前缺口事件的代码不进日线阶段（`:1895-1937`）。
- 生存期（TTL）：`event_ttl_seconds` 86,400 秒（`config.py:173-175`），自 first_seen_at 起算；越界的走保留通道进 EXPIRED，每次最多 40 条（`worker.py:223-228`；`service.py:1280-1284`）。
- 终态单向：FAILED、EXPIRED 不能复活（`repository.py:3026-3037`）。没有别的冷却期。
- 失败条件：完整 K 线收在失效位之下（`service.py:2805-2809`，`:1241-1243`），缺口回补（`:1175-1179`）。开盘区间事件的失效位是当天区间低点，用锚位对象保存（`anchors.py`）。
- 实时成交通道（`realtime.py`）：Finnhub 逐笔在两次扫描之间把 WATCHING 推到 TRIGGERED。它依赖 `quotes.enabled`，`personal.toml` 里为 false。回放不做这条通道。请用数据核实它确实没开：数生产库 `breakout_live_events` 的行数，以及 `breakout_events.event_json` 里 `trigger_source = 'finnhub'` 的行数。
- 影子研究行（`range_persistence_shadow`）和 T1 评估表（`breakout_t1_*`）也按扫描写入。

### 5.2 评估里「一个事件」是什么

- 一个事件 = 一个 `event_id`。评估单位 = 该事件第一次进入触发态的那次扫描。触发态集合是 TRIGGERED、CONFIRMED、HOLDING、RETESTING、RETEST_HELD、REACCELERATING、EXTENDED、FAILED（`repository.py:39-48`）。
- 触发时间 `triggered_at` = 那次扫描的 as_of（转换的 `evidence_at` 就是 observed_at，`service.py:2855-2864`）。
- 触发价 `event_price` = 最后一根完整 5 分钟 K 线的收盘（`feature_engine.py:508-511`，`:551`）。生产研究把它叫「触发时点标记」，不是可成交价（`research_validation.py:24-30`）。回放另记一个可执行入场价：as_of 起那根 5 分钟 K 线的开盘价，分钟库里有。
- 前瞻收益用 D+1、D+5、D+20、D+63 个交易日的收盘（拆股复权的日线），超额对 SPY 同窗口。另记 D+0 收盘看当日走势。
- 盘前缺口事件在盘中第一次满足「缺口守住」时进 TRIGGERED（`service.py:1246`），按同一规则计。
- 同一代码同一日的开盘区间事件与日线基底事件分别计，另给一个按「代码 × 日」去重的视图。
- 只到 WATCHING 的事件不算收益，只进漏斗统计。

### 5.3 用户看到什么

- 页面读 `/api/breakouts/current`，返回最近一次 completed 扫描的全部事件（`api/breakouts.py:763-835`，`repository.latest_completed_scan`）。顺序是 `event_at` 降序、`alert_priority_score` 降序、`event_id` 降序（`repository.py:3235-3245`，`:3869-3875`）。`event_at` 对已触发事件等于 `triggered_at`，对未触发等于 `first_seen_at`。所以**列表基本按触发或发现时间倒序，优先级只在同一次扫描内排序**。
- 页面过滤：生命周期状态、代码、自选，以及分数下限。分数下限比的是 `intrinsic_strength_score`，不是告警优先级（`frontend-src/src/pages/Breakouts.tsx:314-324`）。
- 可选 T1 排序把 T1 满足的事件在同一交易日组内提前（`t1_priority.py:580-604`；`api/breakouts.py:820-821`）；默认是生产排序（`algorithm_modes.py:290-315`）。
- 历史事件分页 `/api/breakouts/events` 同一排序，可按状态、类型、时段、最低优先级过滤（`:838-853`）。
- 对评估的含义：改打分权重几乎不改变默认列表的先后，只通过页面的分数下限和「同一扫描内谁在前」起作用。所以主指标看全部触发事件，次指标看「优先级或强势分高于阈值」的子集，排序类指标放第三位（见 8）。

## 6. 取数规模的细节

### 6.1 记号与上界

- S(D) 用附录 A 的查询算。目录里的资产类型过滤（附录 B）会再减一些，普查先给上界。
- L(D) 的硬上界是 min(30 × 当日盘中扫描数, |超集 ∩ 20 日成交额 ≥ 1,000 万|)。后者附录 A 也给了查询。
- C(D) ≤ L(D−1)（前一日进过盘中阶段的候选都会成为至少 WATCHING 的事件，24 小时内都是遗留事件）。它们的回看窗口与前一日的几乎重合，只多当天。

### 6.2 两遍抓取

1. **第一遍**：按天抓「超集 ∪ 遗留代码」的当天 K 线。用这份数据加日线跑发现与日线两段（不抓回看）。相对量只影响确认与打分，不影响哪 30 只进盘中阶段、也不影响是否建事件（排序键见 `service.py:2315-2323`；触发只看价格对阻力，`breakout_detector.py:133-149`；相对量只进强确认和再加速，`:150-160`，`service.py:1229-1240`）。这一遍产出每天精确的 L(D) 名单。
2. **第二遍**：只给 L(D) 抓回看，去掉第一遍已有的票日。按股票把连续日期合成段。

第一遍的量就是 0.1 表里「只抓当天」那列。

第一遍不是免费的。它省掉的只是相对量的回看，基底检测、持续度、强势分和状态机照常运行，算力约为一次正式回放的一半以上，而且结果只用来产出名单，之后丢弃。另一条路是不做第一遍，直接给 S2（超集 ∩ 20 日成交额 ≥ 1,000 万）抓回看：多抓约 3 倍的回看字节，少跑一遍计算。普查出 S2 与 L 的比值后再定哪条便宜。

### 6.3 存储

- 分钟库：每根 K 线 6 个字段，列式压缩约 30 到 40 字节。五年示意 2.1 百万票日 × 192 根 ≈ 4 亿根，约 12 到 16 GB 未压缩，压缩后约 4 到 5 GB。
- 仓库：`publish_scan` 每次扫描给每个事件存一份完整 JSON（`repository.py:3227-3269`），单份约 30 到 80 KB。不清理的话五年约 109 次 × 60 条 × 50 KB × 1,250 天 ≈ 400 GB，不可行。计划：每天调一次生产的 `prune_retention(scan_days=1)`（`:4137-4345`，保留每个事件最早与首次触发两份快照，正是评估要读的），再由回放脚本在导出每日紧凑账本后删除已导出的保留行（这是回放自己的库，不是生产数据）。仓库稳定在 1 到 2 GB。

## 7. 保真度计划（烟雾测试）

生产不存盘中特征，没有逐值的黄金样本。但最近 90 天的生产库存了三层可比的东西。回放同一时段，逐层比。

### 7.1 需要协调方只读导出的表与列

来源：生产主机上的 `/data/optix.db`（容器内路径，`docs/breakout-radar/architecture.md` 第 3 节）。建议用 `sqlite3 .backup` 复制整库到本机再查，或按下面的列导出。烟雾窗口建议 2026-09-08 到 2026-09-25（14 个交易日），备用窗口 2026-08-11 到 2026-08-22。生产的 `breakout_scan_events` 从 2026 年 7 月中旬开始有数据。

| 表 | 列 | 用途 | 保留期 |
|---|---|---|---|
| `breakout_scan_runs` | `scan_run_id, provider, profile, session, scheduled_at, started_at, completed_at, published_at, status, candidate_count, event_count, error_code, config_hash, versions_hash, versions_json, source_snapshot_id` | 扫描时刻、节奏、设置哈希、成功率 | 90 天 |
| `breakout_candidates` | `scan_run_id, ticker, provider_timestamp, candidate_json` | 发现层黄金样本：`candidate_json` 含 `price, provider_change_pct, provider_volume, provider_relative_volume, provider_market_cap, sector, asset_type, exchange, name, session, previous_regular_close, quality, warnings`（`repository.py:2758-2791`） | 行 90 天；`raw_provider_fields_json` 24 小时后清空 |
| `breakout_provider_snapshots` | `scan_run_id, provider_cache_key, status, as_of, session, candidate_count, warnings_json, payload_json` | 提供方状态（active、degraded、stale）；`payload_json` 24 小时后被替换成 `{"redacted":true}` | 见左 |
| `breakout_structures` | `scan_run_id, pivot_id, ticker, calculation_cutoff_at, structure_json` | 日线阶段黄金样本 | 90 天 |
| `breakout_events` | 全部列 | 事件当前版、`triggered_at`、`first_seen_at` | 永久 |
| `breakout_transitions` | `transition_id, event_id, from_state, to_state, reason, evidence_at, scan_run_id, transition_json` | 首次 TRIGGERED 的证据时间 | 永久 |
| `breakout_scan_events` | `scan_run_id, event_id, rank, ticker, session, setup_type, lifecycle_state, event_at, alert_priority_score, sort_priority, event_snapshot_json` | 每次扫描的事件快照，含 `features`（`event_price, atr20, vwap, rvol_time_of_day, opening_range_high/low, hold_bars_above_pivot, cumulative_dollar_volume, price_data_provenance, detection`）与 `scores` | 90 天内全部保留 |
| `breakout_live_events`、`breakout_live_transitions` | 全部 | 确认实时通道为空 | |
| `breakout_t1_current`、`breakout_t1_evaluations` | 全部 | T1 的黄金样本 | |
| Worker 容器环境变量 | `BREAKOUT_*`、`MARKET_SHAPE_*`、`RANGE_PERSISTENCE_*` | 设置一致性 | |

`breakout_scan_events` 14 天大约 14 × 109 × 60 行、每行几十 KB，整表约 数 GB。若要瘦身，只导每个事件的首行、首次触发行，加每行的 `json_extract` 若干字段（`event_price`、`rvol_time_of_day`、`atr20`、`opening_range_high`、`price_data_provenance.intraday.source`）。

### 7.2 三层比对与判定

**第一层：发现。** 对每次 provider 状态为 active 的生产扫描，在同一 as_of 跑回放的代理发现。
- 集合：150 名单的 Jaccard 相似度；**前 60 名的重合率**（这是进日线阶段的名单）；涨幅排序的 Spearman 相关。
- 数值：共同代码上，涨幅差的分布（预期在一根 K 线的波动内）；相对量之比的分布。用后者做一次性校准：分别按「累计 ÷ 全日均量」与「累计 ÷ 同时点累计均量」两种定义算，哪种与 `provider_relative_volume` 更接近就采用哪种。若同时点口径胜出，0.1 里的成交量必要条件作废。
- 行业：用 `candidate_json.sector` 对附录 C 的映射做混淆矩阵。
- 资产类型：`candidate_json.asset_type` 对附录 B 的映射。
- 判定：前 60 名重合率中位数 ≥ 80%，且漏掉的名单能归因（市值过滤、最新价差一根 K 线、TradingView 用 stale 快照）。

**第二层：日线。** 两边都进了日线阶段的代码：`pivot_id` 相等率。它是基底起止日、阻力区（保留 6 位小数）与版本的哈希，相等就证明日线输入一致。目标 ≥ 95%；不等的比较阻力区上沿的相对差。

**第三层：事件。** 生产的首次触发行（`transitions.to_state = 'TRIGGERED'` 最早一条）对回放：
- 召回：生产触发的事件中，回放在同一代码、同一 `trading_date`、同一 `setup_type` 上也触发，触发时间差不超过一次扫描（盘中 5 分钟、盘前 10 分钟），触发价差不超过 0.5%。目标 ≥ 90%（只算其代码进了回放日线阶段的事件）。
- 精确率：回放触发而生产没有的事件，逐个归因到发现层差异。
- 收盘时状态一致率（TRIGGERED、CONFIRMED、FAILED 等）目标 ≥ 85%。
- 特征：`rvol_time_of_day` 相对误差中位数 < 10%，只在生产 `price_data_provenance.intraday.source` 为 Massive 的扫描上比（Yahoo 兜底的 K 线与 Massive 不同源，`adapters/price_data.py:219-253`）。
- 类型分布：比较 `setup_type` 的比例，尤其盘前缺口类，检查盘前超集的启发式漏了多少。

构造上不可能一致、要在报告里单列的：实时成交通道触发的事件（应为零）；生产用 Yahoo K 线的扫描；生产降级退避造成的扫描间隔空洞；TradingView 返回 stale 快照的扫描；生产的候选表里没有 24 小时前的原始字段。

任何一层不达标，先查原因，再进第二阶段的评估。

## 8. 候选改动、指标与取舍规则（预登记草稿）

### 8.1 基线

- B0：`breakout-score-v1`（`scoring.py:11`）加生产排序，就是今天线上的样子。
- B1：`t1-daily-priority-v1`（`t1_priority.py`；`algorithm_modes.py:34-36`）。它需要 D 日收盘，所以它的名单只在 16:00 之后存在，入场按 D+1 开盘计。

### 8.2 候选（第一阶段不超过 8 个重跑配置）

大多数是设置项，改环境变量就行；两个要在生产代码里加开关（默认关闭，加黄金样本守住默认路径，与 v1.7 同做法）。

| 名称 | 机制 | 理由 | 要不要重跑 |
|---|---|---|---|
| `confirm3` | `BREAKOUT_CONFIRMATION_BARS` 2 改 3（`config.py:167-169`） | 多一根持有 K 线，少一些单根假突破 | 重跑 |
| `chase15` | `BREAKOUT_MAX_CHASE_DISTANCE_ATR` 2.0 改 1.5（`:170-172`） | 更早标记过度延伸，减少追高入场 | 重跑 |
| `orb15`、`orb60` | `BREAKOUT_OPENING_RANGE_MINUTES` 30 改 15 或 60（`:164-166`） | 开盘区间长短决定锚位质量与触发早晚 | 重跑 |
| `disc5` | `BREAKOUT_REGULAR_MIN_CHANGE_PCT` 3 改 5（`:137-139`） | 漏斗更窄，进日线阶段的是更强的动量股 | 重跑（改漏斗） |
| `rvol2` | 强确认里的相对量门槛 1.5 改 2.0（`breakout_detector.py:73`、`:155`，需加开关） | 单根确认要更明确的量能 | 重跑 |
| `adv25` | `BREAKOUT_MIN_AVG_DOLLAR_VOLUME` 1,000 万改 2,500 万（`config.py:134-136`） | 只留流动性更好的名字 | 重跑 |
| `basemin15` | `BREAKOUT_BASE_MIN_DAYS` 10 改 15（`:153`） | 更长的整理更可信 | 重跑 |
| `lookback10` | 相对量回看 20 改 10（`feature_engine.py:358`，需加开关） | 若效果不差，取数减半 | 重跑（也是成本项） |

另有三项只是对基线输出的切分，不用重跑，但必须一起预登记，避免事后挑：
- `noorb`：只看日线基底突破，不看开盘区间突破（按 `setup_type` 切）。
- `mkt_gate`：去掉 `market_eligibility` 为 caution 或 restricted 时的触发（快照里有 `features.market_eligibility`）。
- `tod`：去掉 10:00 之前和 15:30 之后的触发（按 `triggered_at` 切）。

第一阶段过了的候选叠加成 S1，最多再跑 2 个组合配置，与 v1.7 同规则。

### 8.3 指标

- 主指标：每个触发事件的前瞻超额（对 SPY），期限 1、5、20、63 个交易日，入场用「as_of 起那根 5 分钟 K 线的开盘价」，退出用期末日线收盘；按信号日取均值，再对日期做 Newey-West t（滞后 h ÷ 5 − 1，与 v1.6 一致）。同时报告用触发时点标记入场的版本，用来对照生产研究口径。
- 次指标：命中率（超额 > 0 的比例）；每日事件数；触发后到 D+1 收盘进 FAILED 的比例；按 `setup_type`、时段、市场形态状态分组的表；「告警优先级 ≥ 60」和「强势分 ≥ 60」子集的主指标（对应页面的分数下限）；每日按告警优先级取前 10 的主指标（排序类）。
- 漏斗与上限：每天在 150、60、30 三个上限处被截掉的候选数；抽 40 个交易日把上限放到 300、120、60 重跑，看多出来的事件数与它们的前瞻超额，回答「上限有没有挡掉好事件」。
- 删失：退市、并购、改代码按 v1.6 研究包 `evaluate.py` 的观察规则处理，三个敏感性情景（legacy、zero、loss）同号才算稳健。只算价格收益。

### 8.4 取舍规则（草稿，正式版写进 PREREGISTRATION.md）

沿用 v1.7：分 P1（到 2024-12-31）与 P2；主指标两段都比基线高；有完整数据的年份至少 3/4 更好；20 日超额不比基线差 1 个百分点以上；每日事件数不低于基线一半；三个删失情景同号。改漏斗的候选（`disc5`、`adv25`）另加：事件数少了要报告被去掉事件的收益，证明去掉的确实更差。`lookback10` 的判据是「与基线差异不显著」，不是「更好」。都不满足就保持现状，结果照样写。

## 9. 需要协调方确认或提供的清单

1. 在 `replay.sqlite` 上跑附录 A 的三条普查，回填 0.1 的 S、S2 与盘前启发式的规模。
2. 核实汇总日线 `h` 是否含盘前盘后（1.4）。
3. 抓 20 个多日请求，看吞吐是受请求数还是带宽限制（0.1）。
4. 对 TradingView 做一次实时探测，数满足三条件的行里市值 < 2 亿的比例（0.2）。
5. 确认 `ticker_sic.json.gz` 里有没有市值或股数字段（1.3）。
6. 导出 7.1 的表与环境变量；数 `breakout_live_events` 行数与 `trigger_source = 'finnhub'` 的事件数（5.1）。
7. 决定 ^VIX 与 ^TNX 两条序列的来源（4 表「大盘形态」行）。
8. 选窗口 (a)、(b) 或 (c)。

## 第二部分：核实结果与预算方案（第一阶段 b，2026-09-28）

第一部分的正文原样保留。本部分记录 2026-09-28 当天三批新事实（协调方对 Massive 的核实、生产库的只读导出、附录 A 的普查结果），由此对第一部分的修正，以及能装进算力预算的回放方案。第一部分与本部分冲突处，以本部分为准，并在此处点明。

## 10. 三批新事实

### 10.1 协调方对 Massive 与 FRED 的核实

- 汇总日线的最高价、最低价只含 09:30 到 16:00（IOVA、AAPL、NVDA、TSLA，2024-06-03 比对）。1.4 的盘中超集成立；盘前超集只能是启发式。
- 汇总日线的成交量比同一天全部 5 分钟 K 线之和大 6% 到 20%（AAPL 50.08M 对 43.59M 全天、41.86M 正常时段）。
- `/v3/reference/tickers/{T}?date=D` 有截至 D 的 `market_cap`、`share_class_shares_outstanding`、`weighted_shares_outstanding`（IOVA 2023-06-01 市值 1.96e9、股数 224.45M；TWTR 2022-03-15 市值 2.72e10；基金市值为空但有股数）。点时市值可以恢复。
- Massive 的指数接口（`I:VIX`、`I:TNX`）没有权限；FRED 的 `VIXCLS`、`DGS10` 无密钥可取。
- 5 分钟 K 线一次请求可覆盖 52 个交易日（2024-01-02 到 2024-03-15，7 到 10 千根，约 0.84 MB，约 2 秒）；8 路并发约 3.2 个多日请求每秒，约每小时 60 万票日。
- `ticker_sic.json.gz` 只有代码、CIK 与 SIC，没有市值或股数。
- 用户的 Google Drive 有数 TB 空余。原始响应按请求逐个 gzip 冻结并附校验清单（与日线冻结包同法），另建一份紧凑的派生库供回放读取，派生库可以从冻结文件重建。

### 10.2 生产设置

- 生产 `config/personal.toml` 的 `[breakout]`：regular 300、premarket 600、closed 1,800，**`range_persistence_mode = "active"`**（仓库默认是 shadow，生产是本地改过的文件）。代码把 active 映成 enabled（`config.py:20-24`）。
- Worker 环境变量只有 `RANGE_PERSISTENCE_VERSION` 与 `RANGE_PERSISTENCE_VALIDATION_VERSION`，都是 `range-persistence-v1`；没有 `BREAKOUT_*`、`MARKET_SHAPE_*` 覆盖，其余全是代码默认值。
- 烟雾窗口内 1,341 次扫描的 `config_hash` 全部是 `cc09185b00548d9ddafd54086c9944619dcb44dc56d34b3b821c278f6d7a46e5`。我用工作树代码复现了它：`BreakoutSettings(BREAKOUT_RADAR_ENABLED=True, RANGE_PERSISTENCE_MODE="enabled", RANGE_PERSISTENCE_VALIDATION_VERSION="range-persistence-v1", 间隔 300/600/1800, BREAKOUT_SCAN_RETENTION_DAYS=90)` 的 `model_dump(mode="json")` 经 `worker._stable_hash`（`worker.py:56-64`）得到同一个值，模式改成 shadow 或保留天数改成别的都不匹配。回放启动时算一次同样的哈希并与这个值比对，是设置一致的证明。

### 10.3 生产库导出（2026-09-08 到 09-25，13 个交易日，只读）

导出文件在协调方的暂存目录 `radar_prod/radar_export_2026-09-08_2026-09-25.jsonl.gz`（58 MB）。下面的数字都从它算出。

扫描：
- 1,341 次调度，1,140 完成、196 失败、5 因 Worker 重启放弃。失败的 196 次错误类型全是 `ValidationError`（错误码 `scan_failed`），占 15%。回放不会有这些失败；比对只对完成的扫描做。这是生产的缺陷，另行报告。
- 完成的扫描每天盘前 26 到 32 次（一天只有 4 次）、盘中 50 到 66 次。同一时段相邻两次的间隔中位数：盘前 646 秒，盘中 365 秒；设置是 600 与 300。盘中大于 15 分钟的空洞只有 3 处。
- 一次扫描的用时中位数 54 到 69 秒（含网络），事件多时更慢。
- 提供方状态 1,018 次 degraded、122 次 active；degraded 只是带了警告（OTC 代码被资产类型剔除、非法代码）。

发现层（`breakout_candidates`，83,040 行）：
- 盘中每次 TradingView 返回 62 到 128 个候选，中位数 90；盘前中位数 23，最多 116，8% 的盘前扫描超过 60 个。
- **62% 的候选行交易所是 OTC。** 盘中按涨幅取前 60 时，OTC 占中位数 45 个（p10 28、p90 54），留给交易所上市股票的席位中位数只有 15 个（p10 6、p90 32）。606 个事件里只有 3 个是 OTC 代码。
- 资产类型：普通股 45,267、ETF 19,211、ADR 18,562。市值缺失 26%（基金为主）；有市值的行里 11% 低于 4 亿，第 10 百分位 3.66 亿，没有低于 2 亿的（被过滤掉了）。
- 相对量第 10、50、90 百分位 1.52、2.54、9.79：1.5 的门槛正好卡在第 10 百分位。盘中涨幅中位数 7.8%，盘前 6.9%。
- 行业字符串前几位：Miscellaneous 19,799、Finance 10,055、Health Technology 7,426、Technology Services 6,555、Producer Manufacturing 5,871、Electronic Technology 5,702、Non-Energy Minerals 4,249、Retail Trade 3,057。附录 C 的目标就是这套字符串。

事件（`breakout_events` 606 个，`breakout_transitions` 4,157 条，`breakout_scan_events` 39,488 行）：
- 每天新事件 21 到 93 个，中位数约 44。一次扫描处理的事件数 E（`event_count`）中位数 34 到 35、p90 50 到 52、最大 80；其中新事件中位数 1 个、遗留事件中位数 33 个。24 小时内存活事件的池子中位数 33、p90 51、最大 79，从未接近 150 的上限，所以遗留通道没有截断过（13.1 的 k = 1 条件成立）。
- 起源类型：PREMARKET_GAP 304（50%）、OPENING_RANGE_BREAKOUT 164、DAILY_BASE_BREAKOUT 114、MOMENTUM_SPIKE 24。
- 有 TRIGGERED 转换的事件 465 个（77%），按起源：盘前缺口 219（47%）、开盘区间 164（35%）、日线基底 74（16%）、动量 8。触发时刻：开盘后 30 分钟内 52、上午其余 235、下午 179，盘前 0。
- **`triggered_at` 非空的事件有 566 个，比有 TRIGGERED 转换的多 101 个。** 原因在 `repository._upsert_events`：`_TRIGGERED_LIFECYCLE_STATES` 含 FAILED（`repository.py:39-48`），一个从 WATCHING 直接 FAILED 的事件会被盖上 `triggered_at = state_changed_at`（`:2931-2957`）。评估里「触发」必须按 TRIGGERED 转换定义，不能按 `triggered_at`。生产的研究验证用的是 `triggered_at`，这 101 个从未触发的事件会被算进前瞻收益，另行报告。
- 终态：EXPIRED 480、FAILED 121，进入过 CONFIRMED 的 433。FAILED 转换有 1,025 条，落在 68 个事件上，最多一个事件 56 条：一个盘前缺口股失败后，只要 TradingView 下一次仍把它列为候选，新候选通道就按同一身份重建它，再次 WATCHING 到 FAILED（`service.py:2752-2865`；`_upsert_events` 对同为 FAILED 的更新不拒绝）。这是生产行为，回放会照样产生；评估只取每个事件第一条 FAILED。
- 盘中 K 线来源：Massive 37,712 行、Yahoo 1,310 行（74 次扫描）、无 466 行（到期）。相对量的比较天数几乎都是 19（30 个日历日的窗口）。市场形态 BULL_TREND 48%、BULL_PULLBACK 46%、RANGE_DISTRIBUTION 6%。强势分可用 91%，区间持续度 active 83%。
- 实时通道两张表为 0 行，确认没开。

### 10.4 附录 A 的普查（协调方在 Colab 跑，275 秒）

无资产类型与市值过滤，交易日自 2021-10-04 起：(a) 1,250，(b) 751，(c) 500。每日票数中位数 / p90 / 最大；五年票日总数；五年出现过的代码数：

| 规则 | 五年 | 三年 | 两年 |
|---|---|---|---|
| A1 严格（1.03、1.5） | 287 / 522 / 3,145；418,922；15,144 | 291 / 515；257,459；12,537 | 309.5 / 525；180,572；11,301 |
| A1 放宽（1.025、1.4） | 380 / 707 / 3,747；554,398；15,701 | 385 / 684 | 403 / 698 |
| S2 = A1 严格 ∩ ADV20 ≥ 1,000 万 | 79 / 182 / 1,494；126,901；5,750 | 86 / 187；82,239 | 99 / 198；60,989 |
| A3 盘前启发式 | 746.5 / 1,563 / 6,623；1,152,389；16,452 | 769 / 1,527 | 845.5 / 1,608 |
| A1 严格 ∪ A3 | 834.5 / 1,718 / 6,917；1,270,525；16,655 | 857 / 1,647；770,480 | 928 / 1,738；554,511 |

协调方按「只抓需要的日期、同一代码连续日期合成 52 日一页」估的请求数：当天 A1 ∪ A3 加 S2 的 20 日回看，五年 2.60 百万票日、699 千次请求、52 GB 原始 JSON；三年 1.62 百万、410 千次；两年 1.17 百万、287 千次。需要的日期分散，合页只减 3 到 4 倍。

## 11. 对第一部分的修正

### 11.1 区间持续度按 enabled 跑

模式 enabled 打开的路径：正式分取带持续度调整的一份（`service.py:2722-2726`，`:1408-1412`，`:1631-1635`）；强势分适配器也拿到 enabled，内在强势分里持续度成为趋势因子之一（`:2110`；`adapters/strength.py:94-109`）。突破层的调整本身仍是 0，因为 `range_persistence_breakout_interaction_enabled` 默认关（`config.py:227-229`），所以差别主要在强势分。回放用 enabled，并设 `RANGE_PERSISTENCE_VALIDATION_VERSION`（`config.py:310-319` 要求）。第一部分第 4 节末尾「用 disabled 省一半日线阶段算力」的开关作废。

### 11.2 OTC 候选：回放必须先做的决定

TradingView 的 america 市场含 OTC 代码，生产没有按交易所过滤（`normalizer.py:160-224` 只看资产类型、价格、市值）。结果是每次盘中扫描前 60 个席位里中位数 45 个是 OTC，上市股票只有约 15 个进日线阶段；OTC 几乎全被 20 日成交额门槛挡在日线阶段（事件里只有 3 个）。这些 OTC 行在冻结的日线包里没有（`include_otc=false`），普查和超集也都不含它们。两条路：

- **甲：忠实复现，连 OTC 一起抓。** 另抓一遍 `include_otc=true` 的汇总日线（1,250 次请求，约 4 GB）做 OTC 普查；OTC 超集的 5 分钟 K 线；OTC 代码的点时市值（TradingView 对多数 OTC 空壳没有市值，空值不过滤，所以只有有市值的 OTC 才需要）。粗估再加 25 到 30 万次请求，约一天抓取。回放的每次扫描事件数与生产相近（E ≈ 35）。
- **乙：回放基线不含 OTC。** 发现层只在上市代码上重算，前 60 席位全给上市股票。这是「去掉了 OTC 噪音的生产」，不是今天的生产。每次扫描进日线阶段的上市股票从约 15 个变成约 34 个（生产候选里上市股票每次约 34 个，正好不到 60），事件数大约翻一倍（E ≈ 70）。烟雾测试仍能验证上市代码这条流水线：把生产候选表里记录的 OTC 行（含涨幅）原样注入回放的发现名单当席位占用者，两周内不用抓任何 OTC 数据就能复现生产的席位挤压；再单独跑一遍不注入的，就量出「去掉 OTC」本身改变了多少事件。

我建议乙，理由：OTC 占用席位是无意的行为（这些票既进不了日线阶段也不能交易），修复只要在规范化里按交易所剔除；回放基线等于修复后的生产。若用户要先修生产，则乙与生产完全一致。选甲多花一天抓取，换来复现一个大概率会被修掉的行为。由用户定。

### 11.3 盘前扫描不能省，但盘前超集可以收窄

盘前起源的事件占一半，触发占 47%（10.3）。省掉盘前扫描等于换一套算法。第一部分第 14 节的结论不变。

盘前超集 A3（每天 746 只）是取数的最大项，是 A1 的 2.6 倍。收窄的依据：盘前候选每次中位数 23 个，只有 8% 的盘前扫描超过 60 个，所以绝大多数盘前扫描里全部候选都进日线阶段，然后只有 20 日成交额 ≥ 1,000 万的才能成为事件，其余的既不成事件也不挤席位。规则：盘前超集 = A3 ∩（ADV20 ≥ 1,000 万，或 open(D) ≥ 1.05 × close(D−1)）。后一半留给那 8% 大缺口日里可能挤席位的低流动性票。漏掉的比例在烟雾周用生产盘前候选行量出来。预计把 A3 从 746 压到 200 上下（要普查确认）。

### 11.4 其他修正

- **回看窗口**按生产适配器写成 30 个日历日（`adapters/price_data.py:174-175`），不是 20 个交易日；生产快照里 `comparison_sessions` 几乎都是 19。
- **节奏**：生产实际盘中间隔 365 秒、盘前 646 秒（设置 300 与 600），加 15% 的失败扫描。回放网格仍按设置（第 2 节），代表系统的设计行为；烟雾测试用生产真实的 `scheduled_at` 驱动时钟，去掉节奏差别后再比数据与算法；另在烟雾周上把 300 秒网格与真实时刻各跑一遍，报告事件数与触发数的差，若差别大就把历史回放的网格改成 360 秒（每天约 65 次盘中扫描）。
- **触发的定义**按 TRIGGERED 转换（10.3）。FAILED 只取第一条。
- **烟雾比对**只对完成的扫描做；生产一次失败会把某个转换推迟到下一次完成的扫描，所以时间容差改为「不晚于下一次完成的生产扫描」。回放在生产失败时刻产生的事件单列报告。特征比对剔除 74 次用 Yahoo K 线的扫描。
- **成交量口径**（10.1）只影响发现层代理的相对量：分子是 5 分钟 K 线累计量，分母是日线均量，代理值会偏低 6% 到 20%。烟雾测试用 `candidate_json.provider_relative_volume` 拟合一个比例系数；校准前预筛的成交量条件放宽到 1.25 倍。生产其他用到成交量的特征各用一种口径，两边一致：20 日平均成交额、基底成交量收缩、强势分、T1 用日线（`feature_engine.py:216-254`；`base_detector.py:116-121`；`strength/features.py:99-121`；`daily_confirmation.py:89-146`）；当天累计成交额、同时点相对量、VWAP 用 5 分钟 K 线（`feature_engine.py:257-310`，`:355-449`，`:340-352`）。
- **点时市值**恢复，0.2 的偏离 (1) 撤销。代理：扫描时点最新价 × 截至 D 的 `weighted_shares_outstanding`（全部类别合计，接近 TradingView 口径）；基金留空。抽样按市值分层，只对进过超集的代码：< 5 亿每月，5 亿到 20 亿每季，≥ 20 亿每年；抽样点到扫描日之间的拆股按拆股表调整股数。五年约 6 万到 9 万次请求。诊断：报告候选里市值落在 1.6 亿到 2.4 亿之间的比例。
- **^VIX、^TNX 用 FRED**：`VIXCLS` 与 `DGS10 × 10`。VIXCLS 与 ^VIX 收盘同源；DGS10 是财政部固定期限票面收益率，^TNX 是 CBOE 按新发 10 年期国债编的指数，相差几个基点，进入的是可选组里 8 个成分之一（`market_regime.py:286-335`，`:301` 取 20 日水平差），一个 2 基点的差在 0 到 100 的成分上动约 0.4 分。FRED 的「.」要剔除；D 日扫描只用到 D−1 的值（`scanner.py:832-861`）；帧只放 `Close` 列。

## 12. 记忆化：不改生产代码能省什么

两层缓存，都不改生产源文件，输出逐字节相同：

1. **注入的适配器缓存**：行情帧按（代码，完整交易日）建一次；强势分按（代码，完整交易日）缓存 `_score_ticker_frames_sync` 的单只结果再拼回集合（结果只依赖完整日线帧，`scanner.py:2086-2135`）；大盘形态按完整交易日缓存 `compute_market_regime`。
2. **模块级替换纯函数**：服务通过模块全局名调用 `detect_base`、`_scan_range_feature`、`compute_feature_snapshot`、`relative_strength_features`（`service.py:24`，`:34-41`，`:60-63`，`:150-168`），特征引擎通过模块全局名调用 `compute_time_of_day_rvol`（`feature_engine.py:513`）。回放导入后把这些名字换成带缓存的包装：首次调用原函数，同键返回同一结果。仓库测试大量用同样手法。键：`detect_base`（代码，完整交易日，检测器设置），函数内部先按完整交易日裁剪（`base_detector.py:225`），`calculation_cutoff_at` 取自最后一根日线（`:170-172`）；`compute_range_persistence`（代码，完整交易日，参数，分布版本）；`compute_feature_snapshot`（代码，扫描时点，开盘区间分钟数）；`compute_time_of_day_rvol`（代码，扫描时点，回看参数）；`relative_strength_features`（代码，完整交易日，行业 ETF，行业广度）。

校验：烟雾周带缓存与不带缓存各跑一遍，`breakout_scan_events` 的快照 JSON 逐字节比对；任一差异都说明某个键漏了输入。

输入裁剪（同样逐字节可验）：盘中扫描的分钟帧只放正常时段 K 线，盘前扫描只放当天盘前 K 线。盘中路径用时段掩码滤掉盘前盘后（`feature_engine.py:150-155`，`:179-213`；`service.py:544-548`，`:594-604`，`:625-630`），盘前路径对历史天只取当天（`:498-500`）。

**实测**（本机单核，合成数据）：

| 计算 | 输入 | 每只 |
|---|---|---|
| `compute_feature_snapshot` | 21 天 × 192 根 | 41.7 毫秒 |
| 同上 | 21 天 × 78 根（只留正常时段） | 28.6 毫秒 |
| 其中 `compute_time_of_day_rvol` | 同上 | 16.8 毫秒 |
| `compute_feature_snapshot` | 只有当天 78 根 | 7.7 毫秒 |
| 盘前 `compute_feature_snapshot` | 只有当天盘前 66 根 | 6.8 毫秒 |
| `detect_base`、`compute_range_persistence`、强势分 | 两年日线 | 24、43、44 毫秒 |
| `relative_strength_features` | 两年日线 | 1.7 毫秒 |
| `score_breakout` 两次；`BreakoutEvent` 校验与导出；100 KB JSON 序列化 | | 0.1、0.1、1.4 毫秒 |

**缓存之后每次扫描剩下什么**：每个事件不可缓存的部分约 39 毫秒（盘中：特征快照首次 29 毫秒，持有根数与 K 线证据约 5 毫秒，相对强弱、确认、检测、状态机、打分、对象、序列化与写库合计约 5 毫秒）；盘前约 13 毫秒。固定开销每次约 0.3 秒（发现代理、日线阶段裁剪与均量、读遗留事件）。E 按 10.3 的实测：

| 情形 | E | 盘中一次 | 盘前一次 | 每天（77 + 32 次，加日线阶段首次出现与规范股票池约 20 到 40 秒） |
|---|---|---|---|---|
| 与生产一致（含 OTC 席位挤压，方案甲） | 35 | 1.7 秒 | 0.7 秒 | 约 2.8 分钟 |
| 不含 OTC（方案乙） | 70 | 3.0 秒 | 1.1 秒 | 约 5.1 分钟 |

基线单核：五年 58 到 106 核时，三年 35 到 64，两年 23 到 43。

## 13. 候选之间共享阶段

同一进程内，每个扫描时点按配置依次调用各自的服务实例（各自的设置与仓库），共用第 12 节的缓存，缓存按扫描时点清空。

| 候选 | 改哪一段 | 与基线相同的部分 | 事件集合 | 边际成本占基线 |
|---|---|---|---|---|
| `confirm3` | 确认所需根数（`breakout_detector.py:81`，`:160`） | 发现、日线、特征快照全部命中 | 相同身份，状态不同 | 约 0.3 |
| `chase15` | 延伸标记（`:166-167`，`:180-182`） | 同上 | 相同 | 约 0.3 |
| `rvol2` | 强确认的相对量门槛 | 同上 | 相同 | 约 0.3 |
| `orb15`、`orb60` | 开盘区间分钟数 | 发现、日线；相对量子结果命中 | 开盘区间事件身份不同 | 约 0.5 |
| `disc5` | 发现层涨幅门槛 | 候选是基线的子集，日线与特征快照几乎全命中 | 子集 | 约 0.3 |
| `adv25` | 日线阶段成交额门槛 | 同上 | 子集 | 约 0.3 |
| `basemin15` | 基底最短天数 | 基底按天重算很便宜；精炼名单约一半变化 | 基底身份不同 | 约 0.5 |
| `lookback10` | 相对量回看 | 特征快照全部重算 | 相同 | 约 0.9，只在烟雾周试 |

第一阶段 8 个约 3.0 个基线当量，第二阶段 2 个组合约 1.0，加基线共约 5.0；池子没有截断（10.3），各配置的遗留批次一致，缓存命中率不会像先前担心的那样下降，上限约 5.5。

## 14. 抽样日与预热

跨日只靠遗留通道的 150 上限耦合（推导见第一部分 13.1 的原理，这里只写结论）。生产池子最大 79（10.3），从未截断，所以 k = 1 精确：从 D−1 的 04:10 用空仓库起跑到 D 结束，D 上的一切与连续跑逐字节相同。回放仍逐日记录截断标志，出现时按 k = 2 重跑那一天并报告差异。评估单位是「评估日上发生的触发」，含 D−1 事件在 D 的触发。

抽样的算力比例是 (k + 1) ÷ N，事件比例 1 ÷ N，配对 t 值随天数平方根变化。五年连续 1,250 天算 1，每 5 天取 1 天算 0.40（250 天，t 值约 0.45 倍）；连续两年也是 0.40（500 天，t 值约 0.63 倍）。第 12 节之后算力不再是瓶颈，抽样只作备选。合成检查本轮没做，第二阶段写成测试：4 天合成数据、每天 19 次扫描，连续跑与从第 3 天、第 2 天起跑比对第 4 天的快照与转换。

## 15. 盘前扫描

见 11.3：盘前起源占事件一半、触发 47%，不能省。盘前扫描的算力占一天约 12%，当天只放盘前 K 线的裁剪已拿到大部分节省。盘前超集按 11.3 的规则收窄，这是取数上真正省的一步。

## 16. 取数方案

### 16.1 按代码整段抓，不按天抓

普查按「只抓需要的日期」估出五年 699 千次请求（10.4）；需要的日期分散，一页 52 天里只落几个需要的日子。改成按代码抓整段：每只代码从它第一次到最后一次需要的日期，切成 52 日一页，请求数 = Σ ⌈跨度 ÷ 52⌉。五年出现过的代码 15,144 只（未过滤类型），每只至多 25 页，上界 38 万次；按资产类型剔除权证、单位、优先股等后约 11 千只，上界 27 万次，跨度不满五年的代码更少，估 16 到 27 万次，按 3.2 次每秒 14 到 24 小时。三年上界约 13 万次，两年约 8 万次。回看窗口随整段自动覆盖，不再需要第一部分 6.2 的两遍法。请协调方用普查里每只代码的日期列表算出精确页数。

存储：每页 0.84 MB 明文，gzip 后约 0.2 MB；五年 16 到 27 万页约 35 到 55 GB 冻结文件；派生库每票日 2 到 3 KB，约 20 GB。

### 16.2 给协调方重跑普查的过滤规则

- 资产类型（用每周点时目录的 `type`）：保留 CS、OS、ADRC、ADRP、GDR、ETF、ETV、ETS；剔除 ETN、PFD、WARRANT、RIGHT、UNIT、FUND、SP、BOND 与其他。ETF 类再用生产的 `asset_policy.is_leveraged_etf(type, name)` 按名称剔除杠杆基金（在 Colab 上装了仓库就能直接调用）。
- 市值：先用目录 `type` 过滤，再对通过的代码按 11.4 的分层节奏取 `weighted_shares_outstanding`，市值 = D−1 收盘 × 股数 < 2 亿的剔除（普查里用收盘代替扫描时最新价）。
- 盘前：A3 ∩（ADV20 ≥ 1,000 万，或 open ≥ 1.05 × 前收）。
- 回报每条规则的每日中位数、p90、票日总数、代码数，以及 16.1 的页数。
- 方案甲还需要 `include_otc=true` 的汇总日线普查。

### 16.3 与算力的关系

取数的墙钟约 1 天（乙）或 2 天（甲），不花机时单位。抓取可以按年份分批，先抓最近两年，回放先跑近两年，同时继续抓更早的。

## 17. 预算方案

成本模型：基线每天 2.8 分钟（甲，E ≈ 35）到 5.1 分钟（乙，E ≈ 70）单核；全部候选约 5.0 个基线当量（上限 5.5）；只有下游候选（`confirm3`、`chase15`、`rvol2`、`disc5`、`adv25`）约 2.4 个。G4 机器 48 个 vCPU、40 个进程、每小时 8.9 个单位（每核时 0.22 个单位）；高内存 CPU 机器 8 个 vCPU、每小时 0.26 个单位（每核时 0.033 个单位，便宜 6.8 倍，按段切开、分会话续跑）。

| 方案 | 窗口 | 配置 | 核时 | G4 单位（墙钟） | CPU 单位（一台 8 核墙钟） | 取数请求（乙） | 评估天数 |
|---|---|---|---|---|---|---|---|
| A | 两年连续 | 全部候选 | 115 到 215 | 26 到 48（3 到 5 小时） | 4 到 7（14 到 27 小时） | 约 8 万次，7 小时 | 500 |
| B | 五年，每 5 天一天，k = 1 | 全部候选 | 115 到 215 | 26 到 48 | 4 到 7 | 与 D 相同（整段抓不因抽样变少） | 250 |
| C | 五年连续 | 基线加 5 个下游候选 | 140 到 255 | 31 到 57（3.5 到 6.5 小时） | 4.5 到 8.5（17 到 32 小时） | 16 到 27 万次，14 到 24 小时 | 1,250 |
| **D（推荐）** | 五年连续 | 全部候选 | 290 到 530 | 65 到 118（7 到 13 小时） | 9.5 到 17（36 到 66 小时；4 台并行 9 到 17 小时） | 同 C | 1,250 |

固定项：烟雾周（14 天连续，基线带缓存与不带缓存各一遍，注入 OTC 与不注入各一遍，加 8 个候选）约 12 核时，G4 约 3 个单位或 CPU 不到 1 个；点时市值 6 到 8 小时抓取，不算单位。方案甲的算力比表中低约 40%，取数多一天。

**推荐 D，在 CPU 机器上跑，先抓近两年先出近两年的结果。** 理由：
1. 885 个单位的预算下 D 只用 10 到 17 个，估算错一倍也不到 40 个；不需要抽样，遗留状态天然精确，1,250 个评估日覆盖 2022 年的下跌。
2. 取数的墙钟（约一天）是真正的排期成本，与算力无关；按年份分批可以让两年的结果先出来（即方案 A 作为 D 的第一段）。
3. 若用户更在意墙钟，D 放到 G4 是 65 到 118 个单位、7 到 13 小时，也在预算内。
4. B 与 A 算力相同、评估天数只有一半，算力不紧就不选它。

之后的确认步骤不变：采纳的配置在五年上单独再跑一遍（约 2.4 个基线当量），只确认不挑参数。

## 18. 需要协调方做的事（接第一部分第 9 节）

已完成：核实 `h` 口径、多日请求吞吐、SIC 表字段、^VIX 与 ^TNX 来源、生产导出、普查、Drive 容量、设置哈希复现（10.2）。

待办：
1. 把 11.2 的甲、乙两条路和 17 的方案表拿给用户定。
2. 按 16.2 的规则重跑普查，并算 16.1 的页数。
3. 若选甲，再抓 `include_otc=true` 的汇总日线并普查。
4. 需要时补导出 2026-08-11 到 08-22 作第二个烟雾窗口。

## 19. 两处生产缺陷（不在本项目范围，另行处理）

1. 15% 的扫描以 `ValidationError` 失败（10.3）。失败的扫描不发布，用户看到的是上一次的快照，事件的转换会推迟一次。
2. 从 WATCHING 直接 FAILED 的事件被盖上 `triggered_at`（10.3），研究验证会把它们当作已触发事件标注前瞻收益。

## 附录 A：普查查询（在 `replay.sqlite` 上跑）

表结构见 `backend/app/services/eod_limited/market_data.py:89-133`。窗口函数需要 SQLite 3.25 以上，Colab 的 Python 自带版本满足。

A.1 盘中超集 S(D)：

```sql
WITH bars AS (
  SELECT ticker, session_date, high, close, volume,
         LAG(close) OVER (PARTITION BY ticker ORDER BY session_date) AS prev_close,
         AVG(volume) OVER (PARTITION BY ticker ORDER BY session_date
                           ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING) AS avg_vol_10,
         COUNT(volume) OVER (PARTITION BY ticker ORDER BY session_date
                             ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING) AS n_prior
  FROM raw_daily_bars
),
adjusted AS (
  SELECT b.*,
         b.prev_close * COALESCE(s.split_from / s.split_to, 1.0) AS prev_close_adj,
         b.avg_vol_10 * COALESCE(s.split_to / s.split_from, 1.0) AS avg_vol_10_adj
  FROM bars b
  LEFT JOIN splits s ON s.ticker = b.ticker AND s.execution_date = b.session_date
)
SELECT session_date, COUNT(*) AS superset_size
FROM adjusted
WHERE n_prior = 10 AND prev_close_adj > 0
  AND high >= 1.03 * prev_close_adj
  AND volume >= 1.5 * avg_vol_10_adj
  AND high >= 2.0
GROUP BY session_date
ORDER BY session_date;
```

A.1 是严格口径。放宽口径把 `1.03` 改成 `1.025`、`1.5` 改成 `1.4`，两组数都要。查询只对 D 当天执行的拆股调整前收与均量；落在前 10 天窗口内部的拆股会让均量偏高或偏低，对普查这种估算够用，正式抓取名单要按 `_series_from_rows` 的方式复权后再算。

A.2 盘中超集里 20 日平均成交额 ≥ 1,000 万美元的部分（L 的上界 S2）：在 A.1 的 `bars` 里加一列 `AVG(close * volume) OVER (PARTITION BY ticker ORDER BY session_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS adv_20`，最后的 WHERE 再加 `AND adv_20 >= 10000000`。

A.3 盘前启发式超集（若 `h` 不含盘前）：把 A.1 最后的 WHERE 换成 `(open >= 1.02 * prev_close_adj OR high >= 1.05 * prev_close_adj) AND high >= 2.0`，并去掉成交量条件。

三条都请同时给出每日数量的中位数、90 分位与最大值，以及与 A.1 的并集大小（A.1 ∪ A.3 才是每天要抓的当天票日数）。

## 附录 B：Massive 目录 `type` 到生产资产类型

生产从 TradingView 的 `type`、`typespecs` 判定（`normalizer.py:36-57`），允许集合是普通股、ADR、ETF（`:169-171`）。目录码映射草案：

| Massive `type` | 生产 `asset_type` | 进不进漏斗 |
|---|---|---|
| CS、OS | common_stock | 进 |
| ADRC、ADRP、GDR | adr | 进 |
| ETF、ETV、ETS | etf | 进，再过杠杆名称正则（`asset_policy.py:15-17`） |
| ETN | 生产映不到 etf（字符串里没有「etf」），算 unknown | 不进 |
| PFD | preferred | 不进 |
| WARRANT、RIGHT | warrant | 不进 |
| UNIT | unit | 不进 |
| FUND、SP、BOND、其他 | fund 或 unknown | 不进 |

烟雾测试用 `candidate_json.asset_type` 校对这张表。

## 附录 C：SIC 到行业 ETF 的映射草案

目标是让候选的 `sector` 字符串直接命中 `_PROVIDER_SECTOR_BENCHMARKS`（`adapters/universe.py:40-79`）。按 SIC 大类（前两位）分：

| SIC 范围 | 填入的 `sector` 字符串 | ETF |
|---|---|---|
| 10 到 14（采矿）；其中 13（石油天然气开采）与 29（石油精炼）、46（管道） | `energy` | XLE |
| 10、12、14 中的金属与非能源矿产；28 化工中的 281 到 282、286、287；26 纸；32、33 金属与玻璃 | `basic materials` | XLB |
| 15 到 17（建筑）；34 到 38 中的机械、运输设备、仪器（不含 3571 到 3579、3661 到 3679 电子）；40 到 45、47（运输）；73 商业服务中的 7389 | `industrials` | XLI |
| 3571 到 3579、3661 到 3679、3823 到 3829、3670 半导体（357、366、367）；7370 到 7379 软件与数据处理 | `technology` | XLK |
| 2830 到 2836 药品；3841 到 3851 医疗器械；80 医疗服务 | `healthcare` | XLV |
| 60 到 62 银行、证券；63 到 64 保险；67（含 6770 空壳） | `financial` | XLF |
| 48 通信；78 电影；27 出版中的 2711、2721 | `communication services` | XLC |
| 20、21 食品烟草；2840 到 2844 日化；51、54 批发零售（食品）；5411 | `consumer defensive` | XLP |
| 23、25、30、31、37（汽车 3711 到 3716）、39；52 到 59 零售（不含 54）；70、72、75、79 服务、娱乐 | `consumer cyclical` | XLY |
| 49（电力、燃气、水） | `utilities` | XLU |
| 65 房地产；6798 房地产投资信托 | `real estate` | XLRE |
| 其他 | 空 | 走主题兜底 |

这张表是草案，边界组（例如 6770 空壳、2834 药品与 3841 器械）在第二阶段用生产候选表的混淆矩阵校对后再定。半导体只映 XLK 不映 SOXX，因为生产的提供方映射里没有 SOXX（SOXX 只在主题兜底里出现，`universe.py:16`）。
