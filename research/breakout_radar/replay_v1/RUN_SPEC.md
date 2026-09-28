# 运行说明：烟雾周，然后五年全量（方案 D）

约定：`$PY` 是仓库虚拟环境的 python；`$P` 是 `research/breakout_radar/replay_v1`；仓库切到 `claude/radar-replay-v1` 的最新提交。所有命令都不联网；取数由协调方另行完成。

数据可见性按预登记修订 2 的默认值：发现代理读「扫描时刻减 15 分钟」时的完整 K 线（`--tv-delay-minutes 15`），价格适配器让每根 K 线在收盘后 563 秒可见（`--bar-delay-seconds 563`）。两者都取 0 就是「实时数据」敏感性运行。回放宇宙只含普通股与存托凭证：日线库与分钟库里没有 ETF 与 OTC，代码本身还会剔除元数据里交易所为 OTC 的行。

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
    --raw-root /content/minute_raw --out /content/minute_store --chunk-months 3
```

两层：每个代码一个 parquet（盘中阶段按代码取 30 天的 K 线）加 `coverage.parquet`（代码 × 有 K 线的日子）；再按美东日期各一个 `days/<日期>.parquet`（代码、槽位、收盘、成交量），发现代理每天只读今天和前一交易日两个文件，不再逐代码打开 12.5 千个文件。第二遍按 `--chunk-months` 个月一批重读代码文件，内存约每批的 K 线量（3 个月约 1 到 2 GB），全量再加几十分钟。烟雾库上两条路径的输出逐字节相同（README「一致性检查」）。

每个回放进程只把本段窗口（预热日前 37 天到段末）的 K 线留在内存：`--minute-cache-tickers`（默认 800）个代码的窗口切片约每个 1 MB，加两天的槽位表约 50 MB。全量约 12.5 千个代码，预计几十分钟；可以在抓取进行中先建近两年的部分，之后重跑覆盖。烟雾周用 `--tar smoke_minute.tar`（本机已建：1,306 个代码、380 万根 K 线、39 个日文件共 37 MB）。

## 1. 烟雾周（2026-09-08 到 09-25）

本机已经跑完，数字在 README 与 DATA_SPEC 第 20 节；这里记的是怎么跑与怎么判。数据：`replay_smoke_subset.sqlite`、烟雾分钟库、FRED、生产导出。分类、行业、市值取生产候选表里 TradingView 的值（`--metadata production --market-cap production`），时钟按生产真实扫描时刻（`--grid production`）。与生产比对用 `hybrid_otc` 变体（把生产当次扫描里的 OTC 行原样注入，占位与生产一致）。

### 1a. 一致性检查（2026-09-22，预热 1 天）

```
COMMON=(--daily-db /content/data/replay_smoke_subset.sqlite --minute-store /content/minute_store_smoke
  --fred /content/data/fred --export /content/data/radar_export_2026-09-08_2026-09-25.jsonl.gz
  --metadata production --market-cap production --grid production --full-snapshots --on-degraded raise
  --variants baseline --start 2026-09-22 --end 2026-09-22 --warmup 1)
$PY $P/scripts/replay.py "${COMMON[@]}" --memo on  --trim on  --out /content/replay/id_ref      --db-dir /content/db/id_ref
$PY $P/scripts/replay.py "${COMMON[@]}" --memo off --trim on  --out /content/replay/id_memo_off --db-dir /content/db/id_memo_off
$PY $P/scripts/replay.py "${COMMON[@]}" --memo on  --trim off --out /content/replay/id_trim_off --db-dir /content/db/id_trim_off
cmp <(zcat /content/replay/id_ref/baseline/snapshots/2026-09-22.jsonl.gz) <(zcat /content/replay/id_memo_off/baseline/snapshots/2026-09-22.jsonl.gz)
cmp <(zcat /content/replay/id_ref/baseline/snapshots/2026-09-22.jsonl.gz) <(zcat /content/replay/id_trim_off/baseline/snapshots/2026-09-22.jsonl.gz)
```

两个 `cmp` 都必须无输出（zsh 里数组要写成 `"${COMMON[@]}"`，不加引号的 `$COMMON` 不会拆词）。`run.json` 里 `variants.baseline.production_field_hash` 必须等于 `cc09185b…`（不等时脚本在启动时就报错）。

### 1b. 与生产比对（13 天）

```
BASE=(--daily-db /content/data/replay_smoke_subset.sqlite --minute-store /content/minute_store_smoke
  --fred /content/data/fred --export /content/data/radar_export_2026-09-08_2026-09-25.jsonl.gz
  --metadata production --market-cap production --grid production --on-degraded raise
  --start 2026-09-08 --end 2026-09-25 --warmup 0)
$PY $P/scripts/replay.py "${BASE[@]}" --variants hybrid_otc --out /content/replay/smoke_lag --db-dir /content/db/smoke_lag
$PY $P/scripts/replay.py "${BASE[@]}" --variants baseline   --out /content/replay/smoke_lag_baseline --db-dir /content/db/smoke_lag_baseline
$PY $P/scripts/replay.py "${BASE[@]}" --variants hybrid_otc --bar-delay-seconds 0 --tv-delay-minutes 0 \
    --out /content/replay/smoke_realtime --db-dir /content/db/smoke_realtime
CMP=($PY $P/scripts/smoke_compare.py --export /content/data/radar_export_2026-09-08_2026-09-25.jsonl.gz
  --daily-db /content/data/replay_smoke_subset.sqlite --minute-store /content/minute_store_smoke --variant hybrid_otc)
"${CMP[@]}" --replay /content/replay/smoke_lag --start 2026-09-18 --end 2026-09-25 --exclude-etf --out $P/results/smoke/primary_noetf
"${CMP[@]}" --replay /content/replay/smoke_lag --start 2026-09-18 --end 2026-09-25               --out $P/results/smoke/primary
"${CMP[@]}" --replay /content/replay/smoke_lag --start 2026-09-08 --end 2026-09-15 --exclude-current-leveraged --exclude-etf --out $P/results/smoke/secondary_noetf
"${CMP[@]}" --replay /content/replay/smoke_lag --start 2026-09-08 --end 2026-09-15 --exclude-current-leveraged               --out $P/results/smoke/secondary
$PY $P/scripts/discovery_misses.py --export ... --replay /content/replay/smoke_lag --variant hybrid_otc \
    --daily-db ... --minute-store ... --out $P/results/smoke/misses
```

判定（PREREGISTRATION 第 8 节与修订 2）：门槛只对主段 09-18 到 09-25、去 ETF 的比对判定；不去 ETF 的数字一并报告；副段 09-08 到 09-15 加 `--exclude-current-leveraged`，只作报告；09-16、09-17 是部署事故日，不比。`smoke_lag_baseline` 对 `smoke_lag` 给出去掉 OTC 的效果；`smoke_realtime` 给出「实时数据」与生产实际的差距，不参与门槛。

- 相对量比例系数：1.0（生产 ÷ 回放的中位数 0.995 到 1.000，两段都是），正式回放不加 `--relvol-scale`。
- 每次扫描单核用时：约 1.4 到 1.6 秒（含发现、日线充实、盘中精修、落库；两条运行同时跑时量得 1,153 次扫描 1,826 秒、2,306 次扫描 3,106 秒）。代理第二版的日上下文多读一天 K 线，第一天慢几分钟，之后相同。

### 1c. 分类层（目录与 SIC 表到位后）

同 1b，但 `--metadata directory --directory /content/data/massive_directory_2026-09-27 --sic /content/data/industry/ticker_sic.json.gz --market-cap shares --shares /content/data/pit_shares.jsonl`。`smoke_compare` 里的市值比值、行业与类型一致率是这一步的判定。烟雾库里有 ETF（那是按生产候选名单抓的），目录元数据下它们会被当作基金进入宇宙；比对时仍加 `--exclude-etf`。

## 2. 点时股数（可与 1 并行）

```
$PY $P/scripts/pit_shares_requests.py --db /content/data/replay.sqlite --directory /content/data/massive_directory_2026-09-27 \
    --start 2021-10-04 --end 2026-09-25 --out /content/data/pit_round1.csv
# 协调方按 csv 抓取，追加到 pit_shares.jsonl，然后
$PY $P/scripts/pit_shares_requests.py ... --samples /content/data/pit_shares.jsonl --out /content/data/pit_round2.csv
```

重复到输出为空。每行样本至少含 `ticker`、`date` 和 Massive 结果里的 `weighted_shares_outstanding`、`market_cap`。

## 3. 五年全量（方案 D）

段：每段 32 个交易日（40 段覆盖 1,250 天），每段预热 1 天。段清单由下面的循环生成，40 进程并行；每段一个进程、一个输出目录、自己的 SQLite。默认就是生产实际的数据可见性（563 秒、15 分钟），不用再传。

```
FULL=(--daily-db /content/data/replay.sqlite --minute-store /content/minute_store --fred /content/data/fred
  --directory /content/data/massive_directory_2026-09-27 --sic /content/data/industry/ticker_sic.json.gz
  --shares /content/data/pit_shares.jsonl --metadata directory --market-cap shares
  --warmup 1 --on-degraded continue
  --variants baseline,confirm3,chase15,orb15,orb60,disc5,adv25,basemin15)
$PY - <<'EOF' > /content/segments.txt
import sys; sys.path.insert(0, "/content/option-pro/backend")
from datetime import date, timedelta
from app.services.market_calendar import is_trading_day
days = [d for d in (date(2021,10,4) + timedelta(n) for n in range(0, 1900)) if d <= date(2026,9,25) and is_trading_day(d)]
for i in range(0, len(days), 32):
    chunk = days[i:i+32]; print(chunk[0], chunk[-1])
EOF
cat /content/segments.txt | xargs -P 40 -L 1 bash -c '$PY $P/scripts/replay.py "${FULL[@]}" --start $0 --end $1 --out /content/replay/full/seg_$0 --db-dir /content/db/full/seg_$0 --label seg_$0 > /content/replay/full/seg_$0.log 2>&1'
```

「实时数据」敏感性运行同样分段，跑基线和只有在实时数据下才可测的两个相对量候选（预登记修订 3）：`--variants baseline,rvol2,lookback10 --bar-delay-seconds 0 --tv-delay-minutes 0 --out /content/replay/realtime/seg_$0`。

第一段（2021-10-04 起）的预热日落在分钟线可取范围之前，预热为空，该段第一天的遗留状态为空，与生产上线首日相同；结果里标注。

完成后检查每段 `run.json`：`degraded` 必须为空（`continue` 只是为了不让一段中途停下，任何降级都要查明原因并重跑该段）；`truncated_live_lane_days` 非空的段按预登记第 3 节重跑预热 2 天比对。`truncated_days` 里只有到期通道满的日子（每个周一的第一次扫描都会：周五留下的事件周末没人处理，到期通道一次只放 30 条，多出的下一次扫描到期）不用重跑，账本里每次扫描的 `truncation_kind` 分 `expiry_lane` 与 `live_lane`（预登记修订 3）。把 `/content/replay/full/` 同步到 Drive。

预估：按烟雾周的 1.5 秒一次扫描、每天约 109 次扫描、8 个配置共享发现与日线阶段，每天全部配置约 8 到 12 分钟单核；1,250 天 40 进程约 5 到 7 小时；「实时数据」的三个配置再加约 2 小时。这个数没有量过全量库上日上下文的构建时间（每天读两个日文件，烟雾库上一天不到 1 秒），第一段跑完后按 `run.json` 的 `elapsed_s` 校准。

## 4. 输出

每段每个配置：`ledger/<day>.jsonl.gz`（每次扫描的候选、结构、事件、转换的紧凑记录，预热日带 `warmup: true`）、`research_bundle.json.gz`（生产研究加载器读出的事件与影子行、转换、事件头、T1）、`run.json`（哈希、配置差异、延迟设置、降级、截断、缓存命中）。评估脚本（第三阶段）读这些文件，退出与删失规则沿用 v1.6 研究包的 `evaluate.py`。
