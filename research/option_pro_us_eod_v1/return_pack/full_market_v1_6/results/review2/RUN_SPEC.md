# 第二次外部审查后的 Colab 重算步骤（2026-09-28）

只重算账本、结果包和普查；不重跑评分，不重跑回放，不搜参数。评估器 `evaluate.py`、`paired.py` 没有改动，第 5 步用一个阶段证明 `metrics.csv`、`paired.csv`、`primary.csv` 逐字节不变。

## 变量

```bash
REPO=/content/option-pro          # 切到 claude/eod-v1.7 的最新提交（已合并 claude/eod-replay-v1.6）
PY=/content/venv/bin/python       # 上一轮的 venv
DATA=/content                     # 解包 replay_bundle.tar.gz 与 v17_bundle.tar.gz 的根目录
KIT=/content/kit                  # review_regressions.py 所在目录
S16=$REPO/research/option_pro_us_eod_v1/return_pack/full_market_v1_6/scripts
S17=$REPO/research/option_pro_us_eod_v1/return_pack/full_market_v1_7/scripts
R16=$REPO/research/option_pro_us_eod_v1/return_pack/full_market_v1_6/results
R17=$REPO/research/option_pro_us_eod_v1/return_pack/full_market_v1_7/results
DIR=$DATA/data/massive_directory_2026-09-27
DB=$DATA/data/replay.sqlite
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$REPO/backend
```

## 需要的输入（相对 `$DATA`）

| 子路径 | 内容 | 用在 |
|---|---|---|
| `data/replay.sqlite` | 行情缓存库（1,254 个交易日） | 全部 |
| `data/massive_directory_2026-09-27/` | 每周点时证券目录 `YYYY-MM-DD.json.gz` | 全部 |
| `archive_stage1/replay/stage1/` | v1.6 第一阶段 176 天记录（v15、tilt_a、tilt_b、r0、tilt_a_r0） | v1.6 账本、普查 |
| `replay/v17_stage1/` | v1.7 第一阶段 176 天记录（v16、g3、g3x2、g4、full3、full4、cons17、nofund） | v1.7 stage1 账本、普查 |
| `replay/v17_stage2/` | v1.7 第二阶段 148 天记录（v16、cons17+nofund、cons17+nofund+d12m1 等） | v1.7 stage2 账本、普查 |
| `replay/v17_stage3/` | V2 回放 177 个文件（本地 `scratchpad/v2_records/v17_stage3`，上传后放到这里） | V2 账本、普查、第 5 步 |
| `replay/stage2/`、`replay/stage2b/` | v1.6 第二阶段记录 | 这轮不用（没有导出过账本） |

上一轮本机的布局，供对照：`$DATA/data/replay.sqlite`、`$DATA/data/massive_directory_2026-09-27/`、`$DATA/replay/{stage2,stage2b,v17_stage1,v17_stage2,v17_stage3}`、`$DATA/archive_stage1/replay/{stage1,verify_v16}`。本机的 `v17_stage1` 是一个合并好的目录；如果包里是 `v17_stage1_a`、`v17_stage1_b` 两个目录，就把两个都用 `--replay` 传给同一条命令（`export_backtest.py`、`ledger_census.py`、`evaluate.py`、`paired.py` 都接受多个 `--replay`，按日期合并，重复日期的视图不一致会报错退出）。

## 1. 回归检查（修复后，真实仓库）

```bash
cd $REPO && $PY $KIT/review_regressions.py --repo . --output $R16/review2/regression-colab.json
```

期望 `all_passed: true`，退出码 0。

## 2. 备份上一轮的账本终值（普查的 `--before`）

```bash
for d in $R16/stage1_reeval $R17/stage1_reeval $R17/stage2_reeval $R17/stage3_v2_reeval; do
  git -C $REPO show HEAD:${d#$REPO/}/backtest/ledger_end_values.json > $d/backtest/ledger_end_values.before.json
done
```

`.before.json` 只是普查输入，不提交。

## 3. 账本重算（参数与上一轮相同）

```bash
$PY $S16/export_backtest.py --db $DB --directory $DIR --cost-bps 10 \
  --replay $DATA/archive_stage1/replay/stage1 --variants v15,tilt_b --out $R16/stage1_reeval/backtest
$PY $S16/export_backtest.py --db $DB --directory $DIR --cost-bps 10 \
  --replay $DATA/replay/v17_stage1 --variants v16,cons17 --out $R17/stage1_reeval/backtest
$PY $S16/export_backtest.py --db $DB --directory $DIR --cost-bps 10 \
  --replay $DATA/replay/v17_stage2 --variants v16,cons17+nofund --out $R17/stage2_reeval/backtest
$PY $S16/export_backtest.py --db $DB --directory $DIR --cost-bps 10 \
  --replay $DATA/replay/v17_stage3 --variants v16,cons17+nofund,cons17_atr125+nofund --out $R17/stage3_v2_reeval/backtest
```

每条命令同时重写同目录的 `top20_lists.csv` 和 `daily_series.csv`；这两个文件来自没有改动的评估器，`git diff --stat` 里应当不出现。变化的只有 `equity_curves.csv`、`ledger_end_values.json` 和图。

## 4. 结果包

```bash
$PY $S16/result_pack.py \
  --stage stage1=$R16/stage1_reeval:$R16/stage1 \
  --stage stage2=$R16/stage2_reeval:$R16/stage2 \
  --stage stage2b=$R16/stage2b_reeval:$R16/stage2b \
  --compare stock_only=v15:v15:balanced \
  --compare tilt_b_balanced=v15:tilt_b:balanced --compare tilt_b_aggressive=v15:tilt_b:aggressive \
  --compare d12m1_balanced=v15:d12m1:balanced@stage2b --compare d12m1_aggressive=v15:d12m1:aggressive@stage2b \
  --compare d12m1_balanced_v15code=v15:d12m1:balanced@stage2 --compare d12m1_aggressive_v15code=v15:d12m1:aggressive@stage2 \
  --backtest $R16/stage1_reeval/backtest \
  --note "stage2b: the replay named the v1.6 baseline v15 (tilt_b for balanced/aggressive, v1.4 for conservative)" \
  --note "d12m1_*: stage2b (v1.6 code, 148 dates); d12m1_*_v15code: stage2 (v1.5 code, the same dates)" \
  --note "re-evaluated with the 2026-09-28 evaluator (verified exits, censoring, sensitivity scenarios); legacy directories untouched" \
  --note "ledgers regenerated after the second review of 2026-09-28 (unlabelled tail entries, rename-gap splits, missing-bar marks)" \
  --out $R16/result_pack.json
$PY $S16/result_pack.py \
  --stage stage1=$R17/stage1_reeval:$R17/stage1 \
  --stage stage2=$R17/stage2_reeval:$R17/stage2 \
  --stage stage3_v2=$R17/stage3_v2_reeval \
  --stage stage3_v2_vs_v1=$R17/stage3_v2_reeval/vs_v1 \
  --compare cons17_conservative=v16:cons17:conservative \
  --compare stock_only_v16_conservative=v16:v16:conservative \
  --compare S1_conservative=v16:cons17+nofund:conservative \
  --compare S2_conservative=v16:cons17+nofund+d12m1:conservative \
  --compare S2_balanced=v16:cons17+nofund+d12m1:balanced --compare S2_aggressive=v16:cons17+nofund+d12m1:aggressive \
  --compare g3_balanced=v16:g3:balanced --compare full3_balanced=v16:full3:balanced \
  --compare V2_vs_V0=v16:cons17_atr125+nofund:conservative \
  --compare V2_vs_V1=cons17+nofund:cons17_atr125+nofund:conservative \
  --backtest $R17/stage1_reeval/backtest --backtest $R17/stage2_reeval/backtest --backtest $R17/stage3_v2_reeval/backtest \
  --note "V0 = v16, V1 = cons17+nofund, V2 = cons17_atr125+nofund (176 dates, replay v17_stage3); V2 fails the 修订 2 rule against V1" \
  --note "atr_distribution.csv in stage3_v2_reeval: production ATR% of the listed conservative stocks per variant" \
  --note "re-evaluated with the 2026-09-28 evaluator (verified exits, censoring, sensitivity scenarios); legacy directories untouched" \
  --note "ledgers regenerated after the second review of 2026-09-28 (unlabelled tail entries, rename-gap splits, missing-bar marks)" \
  --out $R17/result_pack.json
```

## 5. 标签不变的逐字节比较（一个阶段）

```bash
mkdir -p /content/check
$PY $S17/evaluate.py --db $DB --directory $DIR --replay $DATA/replay/v17_stage3 --out /content/check/stage3_v2_reeval
$PY $S17/paired.py   --db $DB --directory $DIR --replay $DATA/replay/v17_stage3 --out /content/check/stage3_v2_reeval
for f in metrics.csv paired.csv primary.csv; do
  cmp /content/check/stage3_v2_reeval/$f $R17/stage3_v2_reeval/$f && echo "$f identical"
done
```

三个都要打印 `identical`。`rules.json` 里写着目录的绝对路径，机器不同就不同，不比较。

## 6. 普查

```bash
$PY $S16/ledger_census.py --db $DB --directory $DIR --replay $DATA/archive_stage1/replay/stage1 --variants v15,tilt_b \
  --before $R16/stage1_reeval/backtest/ledger_end_values.before.json --after $R16/stage1_reeval/backtest/ledger_end_values.json \
  --out $R16/review2/census_stage1
$PY $S16/ledger_census.py --db $DB --directory $DIR --replay $DATA/replay/v17_stage1 --variants v16,cons17 \
  --before $R17/stage1_reeval/backtest/ledger_end_values.before.json --after $R17/stage1_reeval/backtest/ledger_end_values.json \
  --out $R17/review2/census_stage1
$PY $S16/ledger_census.py --db $DB --directory $DIR --replay $DATA/replay/v17_stage2 --variants v16,cons17+nofund \
  --before $R17/stage2_reeval/backtest/ledger_end_values.before.json --after $R17/stage2_reeval/backtest/ledger_end_values.json \
  --out $R17/review2/census_stage2
$PY $S16/ledger_census.py --db $DB --directory $DIR --replay $DATA/replay/v17_stage3 --variants v16,cons17+nofund,cons17_atr125+nofund \
  --before $R17/stage3_v2_reeval/backtest/ledger_end_values.before.json --after $R17/stage3_v2_reeval/backtest/ledger_end_values.json \
  --out $R17/review2/census_stage3_v2
```

每个输出目录有 `census.json`（每条账本的计数：B 尾部被跳过的入场、C1 更名空档拆股、C2 缺行情日拆股，以及近似的净值影响）、`census_cases.csv`（逐案）、`end_value_changes.csv`（每条账本修复前后的终值、相对 SPY 槽和被动 SPY 的差，以及期末开放持仓与现金比例）。计数为零就如实是零。

## 7. 送回

- 四个 `backtest/` 目录里的 `ledger_end_values.json`、`equity_curves.csv`（大文件）和图；
- 两个 `result_pack.json`；
- `review2/regression-colab.json`、四个 `census_*` 目录、第 5 步的三行 `identical`；
- `git -C $REPO status --short`，确认 `top20_lists.csv`、`daily_series.csv` 没有变化。

大明细（`equity_curves.csv`）是既有文件的重生成，原地覆盖；以后新增的大输出按审查意见放到仓库外。
