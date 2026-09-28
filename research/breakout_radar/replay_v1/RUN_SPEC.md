# 运行说明：烟雾周，然后五年全量（方案 D）

约定：`$PY` 是仓库虚拟环境的 python；`$P` 是 `research/breakout_radar/replay_v1`；仓库切到 `claude/radar-replay-v1` 的最新提交。所有命令都不联网；取数由协调方另行完成。带「待填」的数字在烟雾测试之后补。

## 0. 数据布局（Colab）

```
/content/data/replay.sqlite                      # 日线冻结（选股 v1.6 同一份）
/content/data/replay_smoke_subset.sqlite         # 烟雾周用的子集（本机也有）
/content/data/massive_directory_2026-09-27/      # 每周点时目录 YYYY-MM-DD.json.gz
/content/data/industry/ticker_sic.json.gz        # SIC 表
/content/data/fred/VIXCLS.csv, DGS10.csv         # FRED
/content/minute_raw/YYYY-MM/<TICKER>_<from>_<to>_p<n>.json.gz  # 原始分钟页
/content/minute_manifest.jsonl                   # 页清单
/content/data/pit_shares.jsonl                   # 点时股数样本（每行一个 /v3/reference/tickers/{T}?date= 结果，附 ticker 与 date）
/content/minute_store/                           # 派生库（第 1 步生成）
/content/replay/<run>/                           # 输出
```

### 第 1 步：建派生库

```
$PY $P/scripts/build_minute_store.py --manifest /content/minute_manifest.jsonl \
    --raw-root /content/minute_raw --out /content/minute_store
```

每个代码一个 parquet 加 `coverage.parquet`（代码 × 有 K 线的日子）。全量约 12.5 千个代码，预计几十分钟；可以在抓取进行中先建近两年的部分，之后重跑覆盖。烟雾周用 `--tar smoke_minute.tar`。

## 1. 烟雾周（2026-09-08 到 09-25）

数据：`replay_smoke_subset.sqlite`、烟雾分钟库、FRED、生产导出。分类、行业、市值取生产候选表里 TradingView 的值（`--metadata production --market-cap production`），时钟按生产真实扫描时刻（`--grid production`）。

### 1a. 一致性检查（两天：2026-09-08、2026-09-09）

```
COMMON="--daily-db /content/data/replay_smoke_subset.sqlite --minute-store /content/minute_store_smoke \
  --fred /content/data/fred --export /content/data/radar_export_2026-09-08_2026-09-25.jsonl.gz \
  --metadata production --market-cap production --grid production --full-snapshots --on-degraded raise"
$PY $P/scripts/replay.py $COMMON --start 2026-09-08 --end 2026-09-09 --warmup 0 --variants baseline --memo on  --trim on  --out /content/replay/id_on   --db-dir /content/db/id_on
$PY $P/scripts/replay.py $COMMON --start 2026-09-08 --end 2026-09-09 --warmup 0 --variants baseline --memo off --trim on  --out /content/replay/id_memo_off --db-dir /content/db/id_memo_off
$PY $P/scripts/replay.py $COMMON --start 2026-09-08 --end 2026-09-09 --warmup 0 --variants baseline --memo on  --trim off --out /content/replay/id_trim_off --db-dir /content/db/id_trim_off
cmp <(zcat /content/replay/id_on/baseline/snapshots/2026-09-08.jsonl.gz) <(zcat /content/replay/id_memo_off/baseline/snapshots/2026-09-08.jsonl.gz)
cmp <(zcat /content/replay/id_on/baseline/snapshots/2026-09-09.jsonl.gz) <(zcat /content/replay/id_trim_off/baseline/snapshots/2026-09-09.jsonl.gz)
```

两个 `cmp` 都必须无输出。`run.json` 里 `variants.baseline.production_field_hash` 必须等于 `cc09185b…`（不等时脚本在启动时就报错）。

### 1b. 与生产比对（13 天）

```
$PY $P/scripts/replay.py $COMMON --start 2026-09-08 --end 2026-09-25 --warmup 0 \
    --variants baseline,hybrid_otc,confirm3,chase15,orb15,orb60,disc5,adv25,basemin15,rvol2,lookback10 \
    --out /content/replay/smoke --db-dir /content/db/smoke
$PY $P/scripts/smoke_compare.py --export /content/data/radar_export_2026-09-08_2026-09-25.jsonl.gz \
    --replay /content/replay/smoke --variant hybrid_otc --out $P/results/smoke/hybrid_otc
$PY $P/scripts/smoke_compare.py --export ... --replay /content/replay/smoke --variant baseline --out $P/results/smoke/baseline
```

`hybrid_otc` 对生产，按 PREREGISTRATION 第 8 节的门槛判定；`baseline` 对生产给出去掉 OTC 的效果（每日事件数之比）。`summary.json` 的 `discovery.relvol_ratio_prod_over_replay.p50` 是相对量比例系数，填到下面。

- 相对量比例系数：待填（写入正式回放的 `--relvol-scale`）。
- 每次扫描单核用时（`run.json` 的 `elapsed_s` 除以扫描数）：待填。

### 1c. 分类层（目录与 SIC 表到位后）

同 1b，但 `--metadata directory --directory /content/data/massive_directory_2026-09-27 --sic /content/data/industry/ticker_sic.json.gz --market-cap shares --shares /content/data/pit_shares.jsonl`。`smoke_compare` 里的市值比值、行业与类型一致率是这一步的判定。

## 2. 点时股数（可与 1 并行）

```
$PY $P/scripts/pit_shares_requests.py --db /content/data/replay.sqlite --directory /content/data/massive_directory_2026-09-27 \
    --start 2021-10-04 --end 2026-09-25 --out /content/data/pit_round1.csv
# 协调方按 csv 抓取，追加到 pit_shares.jsonl，然后
$PY $P/scripts/pit_shares_requests.py ... --samples /content/data/pit_shares.jsonl --out /content/data/pit_round2.csv
```

重复到输出为空。每行样本至少含 `ticker`、`date` 和 Massive 结果里的 `weighted_shares_outstanding`、`market_cap`。

## 3. 五年全量（方案 D）

段：每段 32 个交易日（40 段覆盖 1,250 天），每段预热 1 天。段清单由下面的循环生成，40 进程并行；每段一个进程、一个输出目录、自己的 SQLite。

```
FULL="--daily-db /content/data/replay.sqlite --minute-store /content/minute_store --fred /content/data/fred \
  --directory /content/data/massive_directory_2026-09-27 --sic /content/data/industry/ticker_sic.json.gz \
  --shares /content/data/pit_shares.jsonl --metadata directory --market-cap shares --relvol-scale <待填> \
  --warmup 1 --on-degraded continue \
  --variants baseline,confirm3,chase15,orb15,orb60,disc5,adv25,basemin15,rvol2"
$PY - <<'EOF' > /content/segments.txt
import sys; sys.path.insert(0, "/content/option-pro/backend")
from datetime import date, timedelta
from app.services.market_calendar import is_trading_day
days = [d for d in (date(2021,10,4) + timedelta(n) for n in range(0, 1900)) if d <= date(2026,9,25) and is_trading_day(d)]
for i in range(0, len(days), 32):
    chunk = days[i:i+32]; print(chunk[0], chunk[-1])
EOF
cat /content/segments.txt | xargs -P 40 -L 1 bash -c '$PY $P/scripts/replay.py $FULL --start $0 --end $1 --out /content/replay/full/seg_$0 --db-dir /content/db/full/seg_$0 --label seg_$0 > /content/replay/full/seg_$0.log 2>&1'
```

第一段（2021-10-04 起）的预热日落在分钟线可取范围之前，预热为空，该段第一天的遗留状态为空，与生产上线首日相同；结果里标注。

完成后检查每段 `run.json`：`degraded` 必须为空（`continue` 只是为了不让一段中途停下，任何降级都要查明原因并重跑该段）；`truncated_days` 非空的段按预登记第 3 节重跑预热 2 天比对。把 `/content/replay/full/` 同步到 Drive。

预估（待烟雾测试校准）：每天基线约 5 分钟单核（DATA_SPEC 第 12 节），全部候选约 5 个基线当量，40 进程约 7 到 13 小时。

## 4. 输出

每段每个配置：`ledger/<day>.jsonl.gz`（每次扫描的候选、结构、事件、转换的紧凑记录，预热日带 `warmup: true`）、`research_bundle.json.gz`（生产研究加载器读出的事件与影子行、转换、事件头、T1）、`run.json`（哈希、配置差异、降级、截断、缓存命中）。评估脚本（第三阶段）读这些文件，退出与删失规则沿用 v1.6 研究包的 `evaluate.py`。
