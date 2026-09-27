# 全市场选股 v1.7：候选开关的生产代码回放

v1.6 用生产代码回放历史，证明了只看股票名单和 tilt_b。v1.7 在同一套回放上测下一批候选：行业因子 G 的两种接法、保守档换口径、打分池去掉基金。候选、指标和取舍规则先写进了 [PREREGISTRATION.md](PREREGISTRATION.md)，本文件记录方法、运行方式和结果（结果部分在回放跑完后填写）。

## 与 v1.6 的差别

- **候选是生产代码里的开关，不是回放脚本里的公式**。`backend/app/services/eod_limited/options.py` 的 `ScoringOptions` 选行业模式和挂钩策略，`live_config.py` 的 `LiveConfig` 是生产的开关板（默认全部是 v1.6 行为），`market_registry.load_market_registry(extra_tilt_multipliers=...)` 承接倾斜，`universe.select_all_market_universe(fund_scope=...)` 承接基金范围。回放脚本 `scripts/replay.py` 只是按方案名打开这些开关。
- **默认关闭时与 v1.6 逐字节一致**：`tests/test_eod_v16_default_identity.py` 用 v1.6 代码（提交 3de252f9）生成的九组视图黄金样本守住默认路径；`tests/test_eod_v17_switches.py` 逐个开关验证行为；`tests/test_full_market_v17_replay.py` 验证回放机制（方案合成、输入共享、开关生效、评估表）。
- **行业分类数据**：`ticker_sic.json.gz`（2026-09-27 冻结）。生产上的取法在 `backend/app/services/eod_limited/industry.py` 的模块说明里：数据目录下一张持久化表（`industry-sic-v1.json.gz`），每次运行按预算补查目录里没见过的股票（Massive `/v3/reference/tickers/{ticker}` 的 `sic_code`），查不到不阻塞发布；`scripts/eod_industry_table.py import` 可以用冻结表做种子。选这条路而不是纯静态表或纯运行时查询的原因：静态表会随上市变化而过期；纯运行时查询第一次要查约 7,000 只，在客户端 4 路并发下约 10 分钟，超出任务的首轮时间预算。

## 回放方法

`scripts/replay.py` 对每个回放日 T：

1. 在当周点时目录上调用 `select_all_market_universe`；取 370 个交易日，`_session_manifest`、`_load_panel`、`prepare_limited_panel`。
2. 用冻结表按（代码, CIK）给当天面板里的股票贴 3 位和 4 位 SIC 分类（`SicTable.classify`）。
3. 把方案按输入集分组：基础输入（`v16`、`g3`、`g3x2`、`g4`、`cons17`、`d12m1`）、`full` 模式各粒度一份（`full3`、`full4`，`prepare_limited_panel(industry=...)` 后再 `precompute_all_horizon_inputs`）、基金范围一份（`nofund`）。每组各算一次预计算，组内共用；算完一组就释放，再算下一组。
4. 每个方案按自己的注册表（倾斜）和 `ScoringOptions` 调 `score_eod_session`，只打分该方案登记的档位；`_compact_variant`、`project_strength_payload`，再按 `/api/strength/scan` 的口径过滤排序。每行记下 3 位 SIC 的 `industry_id`，供「无分类比例」诊断使用。
5. 带 `residual` 的方案（`d12m1` 及其组合）用 506 日面板重算 D 家族残差再替换进输入，面板带不带行业跟随该方案的模式。

方案名可以用 `+` 合成（`g3+cons17`、`g3+cons17+d12m1`）：开关合并、档位取并集，同一开关被设两次会报错。第二阶段的组合就用这个写法，不新增代码。

`scripts/evaluate.py`、`scripts/paired.py` 与 v1.6 同口径，另加：基线名 `--baseline v16`；保守档候选与基线保守档比；预登记的稳健性配对（`g3`/`g4`、`full3`/`full4`）；无分类比例诊断；`nofund` 的逐日一致性检查（`identity.json`）；`--replay` 可以重复给出，把两台机器各跑一半日期的目录合并（共有的视图必须逐行一致，否则报错）。`scripts/compare_records.py` 逐行比对两次回放。

## 在 Colab 上运行

前提：G4 机器上已有 v1.6 阶段用的 `/content/data/replay.sqlite`、`/content/data/massive_directory_2026-09-27/` 和 venv；仓库切到 `claude/eod-v1.7` 分支的最新提交。另外把冻结的行业表放到 `/content/data/industry/ticker_sic.json.gz`（就是 scratchpad/industry/ 下那份，sha256 见同目录 manifest.json）。

下面 `$PY` 是 venv 的 python，`$S` 是 `research/option_pro_us_eod_v1/return_pack/full_market_v1_7/scripts`。

### 第 0 步：核对基线一致（两台机器都跑）

```
$PY $S/replay.py --db /content/data/replay.sqlite --directory /content/data/massive_directory_2026-09-27 \
  --industry-table /content/data/industry/ticker_sic.json.gz --out /content/replay/v17_verify \
  --dates 2023-05-30,2024-10-18,2026-05-27 --variants v16 --workers 3
$PY $S/compare_records.py --left /content/replay/verify_v16 --right /content/replay/v17_verify --map v15=v16
```

`/content/replay/verify_v16` 代表 v1.6 阶段用 v1.6 代码回放这三天的输出目录（当时的方案名是 `v15`，按实际路径替换）。必须打印 `views compared 27, identical 27`，退出码 0；否则停下来，把差异发回来。三天并行约 7 分钟。

### 第 1 步：第一阶段（两台机器各一半日期）

日期清单是 v1.6 第一阶段的 176 天（`stage1_run.json` 的 `dates`，把这个文件放到当前目录）。机器 A 跑偶数位、机器 B 跑奇数位，两边都带上第 0 步的三天，作为跨机器确定性检查：

```
# 机器 A
$PY $S/replay.py --db /content/data/replay.sqlite --directory /content/data/massive_directory_2026-09-27 \
  --industry-table /content/data/industry/ticker_sic.json.gz --out /content/replay/v17_stage1_a \
  --dates $(python3 -c "import json;d=json.load(open('stage1_run.json'))['dates'];print(','.join(sorted(set(d[0::2])|{'2023-05-30','2024-10-18','2026-05-27'})))") \
  --variants v16,g3,g3x2,g4,full3,full4,cons17,nofund --workers 40
# 机器 B：同上，d[1::2]，--out /content/replay/v17_stage1_b
```

每个回放日一个进程的预估（按 v1.6 日志的 330 到 510 秒预计算、每 6 组视图 105 到 160 秒打分推算）：
- 载入 20 秒；基础预计算 330 到 510 秒；`full3`、`full4` 各再一份（行业篮子只多约 5%）；`nofund` 一份，约一半大小；
- 打分：`v16` 9 组约 160 到 240 秒，`g3`、`g3x2`、`g4` 各约 110 到 160 秒，`full3`、`full4` 各 9 组约 160 到 240 秒，`cons17` 3 组约 60 秒，`nofund` 9 组约 160 到 240 秒；
- 合计约 40 到 55 分钟一天一核。每台 91 天、40 进程，约 2.5 轮，预计 1.7 到 2.3 小时。内存：每个进程同一时刻只持有一份预计算。

如果只有一台机器，把全部 176 天交给它，预计 3.5 到 4.5 小时；或者先去掉 `full4`，省约四分之一。

### 第 2 步：评估

```
$PY $S/evaluate.py --db /content/data/replay.sqlite --replay /content/replay/v17_stage1_a --replay /content/replay/v17_stage1_b --out results/stage1
$PY $S/paired.py   --db /content/data/replay.sqlite --replay /content/replay/v17_stage1_a --replay /content/replay/v17_stage1_b --out results/stage1
```

`evaluate.py` 合并两台机器的日期，三个重复日期上的 `v16` 视图必须一致，否则它会报错退出。产出 `metrics.csv`、`primary.csv`、`decision.json`（含 `pairs`）、`identity.json`、`label_status.json`；`paired.py` 产出 `paired.csv`。

### 第 3 步：第二阶段（按预登记决定配置后）

组合用 `+` 写，例如第一阶段 `g3` 和 `cons17` 通过：

```
$PY $S/replay.py ... --out /content/replay/v17_stage2 --variants v16,g3+cons17,g3+cons17+d12m1 --workers 40 \
  --start 2023-10-06 --end 2026-09-18 --every 5
```

`d12m1` 需要 506 日窗口，只能从 2023-10-06 起（148 天），与 v1.6 第二阶段相同；两个组合共用基础预计算，`d12m1` 那个每天多约 130 到 220 秒。评估时 `--dates-from 2023-10-06`，并把第一阶段目录一起传入，基线和单项候选就在同一批日期上可比。

## 结果

待第一阶段回放。
