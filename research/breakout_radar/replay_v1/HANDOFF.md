# 突破雷达回放：交接说明（2026-09-29）

写给下一位接手的人（或代理）。先读这份，再按需要读同目录的 README、DATA_SPEC、PREREGISTRATION、RUN_SPEC。

## 1. 现在到哪一步了

- **选股（screener）这条线已经收尾。** v1.6、v1.7 已合并并部署到生产（74.91.31.186），对应 yayoihq8964/option-pro PR #200（782e1169）和 #201（35ab1395）。剩两件小事没做：部署后页面上的版本标签不对；前端没有显示拒绝理由。
- **突破雷达（breakout radar）这条线在研究阶段。**
  - 目标：用生产代码逐次回放五年的历史扫描，比较 8 个候选配置和基线，按预登记的规则决定采纳哪个。
  - 保真度已经验证过（第 3 节）。
  - 五年全量回放在一台 Colab G4 上只跑完了一部分交易日，原因是会话有 24 小时上限，磁盘也不够（第 4 节）。
  - 评估只在已完成的交易日上做（预登记修订 5）。
- **生产上的雷达代码改动还没部署。** 本分支有三个提交会改变生产行为（第 2 节），都要用户确认后才能上线。

## 2. 分支与提交

- 工作树：`/Users/admin/Downloads/Claude/option-pro-radar`。
- 分支：`claude/radar-replay-v1`，基于 `origin/main` 35ab1395，**没有推送到远端**。
- 不要动 `/Users/admin/Downloads/Claude/option-pro`（用户自己在改的检出）和 `option-pro-ui`。

会改变生产行为的提交：

| 提交 | 内容 |
|---|---|
| 85371bcc | 发现层默认排除 OTC 代码：TradingView 查询加 `exchange != OTC`，规范化再兜底一次；`BREAKOUT_ALLOW_OTC` 打开才保留 |
| 6d883b6e | 研究开关改走私有属性；默认路径和 `config_hash` 不变 |
| 20b6d3f0 | 发现层默认排除普通 ETF：查询只留 `type in (stock, dr)`，规范化再兜底一次；`BREAKOUT_ALLOW_ETF` 打开才保留（杠杆基金本来就排除） |

- 测试结果：
  - 20b6d3f0 时，雷达相关 480 项、后端全套 4,839 项通过。
  - a20c5769 时，`-k "breakout or radar"` 486 项通过。
- 部署这三个提交后，生产新扫描的 `breakout_scan_runs.config_hash` 应当等于 `76cf81ce3da0f09b30cd8aa81decbb31ef8eb88007ece722b69fdbbabf2ecb12`，这是回放基线的全字段哈希。
- 九月生产的哈希 cc09185b… 是旧口径（`allow_etf` 为真）。

其余提交都在 `research/breakout_radar/replay_v1/`：
- 数据规格、预登记（修订 1 到 5）、运行说明；
- 回放骨架 `harness/`：运行器、发现代理、数据存储、设置与哈希、评估；
- 脚本 `scripts/`；
- 这次在 Colab 上用的操作脚本 `ops/2026-09-29/`（第 6 节）。

## 3. 保真度（已完成，结论可用）

回放把 TradingView 的发现层换成代理，用冻结的 Massive 5 分钟 K 线、点时股数和目录元数据复现，其余全是生产代码。

- 延迟设置：K 线延迟 563 秒，TradingView 延迟 15 分钟。
- 扫描节奏：每天 109 次扫描。

与九月生产导出（2026-09-08 到 09-25）比对：

- **生产元数据下（1b）**：发现召回 92.1%。
- **分类层也换成代理后（1c，变体 `hybrid_etf`）**：
  - 主段召回 92.9%（117/126），副段 100%（87/87）。
  - 市值比值中位数 1.0035（p10 0.92，p90 1.095）。
  - 前 60 名重合中位数 1.00。
  - 最终状态一致 94.2%，结构一致 94.3%，T1 一致 42/44。
  - 详见 DATA_SPEC 20.14。
- **已知的系统差异**：TradingView 的 `market_cap_basic` 在跳空日会滞后于价格。
  - 例子：SRZN 2026-09-24 被生产的 2 亿市值门槛悄悄剔掉一整天。
  - 回放用的是点时股数乘价格，所以在 2 亿边界的跳空日会比生产多列几只（DATA_SPEC 20.13）。
  - 可以在线核实：挑几只当天大涨的股票，对比 `market_cap_basic` 和「最新价 × 股数」是否同步。

## 4. 五年全量回放（2026-09-28 到 09-29，Colab G4）

### 怎么跑的

- **时间范围**：2021-10-04 到 2026-09-25，按 32 个交易日切成 40 段；最后一段只有 09-24、09-25 两天。每段前面多跑 1 天预热，预热日不计入评估。
- **配置**：8 个，`baseline,confirm3,chase15,orb15,orb60,disc5,adv25,basemin15`，使用生产的数据可见性。
- **宇宙**：上市普通股和存托凭证，不含 ETF、OTC（修订 4）。
- **代码**：运行用 3aaa04d1，放在 G4 的 `/content/option-pro`。
- **编排**：`ops/2026-09-29/run_full.py`，40 个进程并行，失败的段重试一次。
- **开始时间**：2026-09-28 18:07 UTC。

### 实测，下次估算要用

- **速度**：每段每小时约 1.22 个交易日（中位数）。近两年的段最慢，每小时 0.5 到 0.7 天。要把全部天数跑完：中位段约 27 小时，最慢的段约 65 小时。一台 G4 的 24 小时会话跑不完。
- **磁盘**：每个配置一份生产 SQLite 库，按「段 × 天」增长。8 个配置合计每「段·天」约 0.28 GB，40 个进程加起来每小时约 12 GB；开跑 7.6 小时后已有 94 GB。
  - G4 磁盘 236 GiB。系统镜像和云端硬盘缓存约占 60 GiB，分钟库 14 GB，留给库的约 150 GB。
  - 40 段同时跑满 33 天，库的峰值约 350 GB，一次跑不下。同时跑的段数要按「剩余磁盘 ÷（每段天数 × 0.28 GB）」来定；近两年每天的量更大，要留余量。段跑完、导出研究数据包之后就可以删库。
  - 运行器每天做一次清理（原始载荷 1 小时、扫描附件 1 天），已经很激进；库涨的主要是事件历史，这部分不能删，否则回放就不再忠实于生产。
- **内存**：每个进程约 2.7 GB，40 个进程合计约 110 GB，G4 共 176 GB。

### 怎么停的

- `ops/2026-09-29/g4_guard.sh` 每分钟检查一次，满足下列任一条件就停：
  - 磁盘剩余低于 12 GB；
  - 可用内存低于 6 GiB；
  - 到 14:30 UTC；
  - 回放进程已经全部退出。
- 停之前先把 `scripts/replay.py` 改名为 `replay.py.stopped`：`run_full.py` 会重试失败的段，被杀的段若重试，会从第一天重来。
- 停的方式：先发 `SIGTERM`，60 秒后仍在的进程用 `SIGKILL`。当天没写完的记录丢弃；判定规则见 RUN_SPEC 第 6 节和修订 5。
- 3aaa04d1 直接写目标文件，杀在写入中可能留下截断的日文件，评估会整日作废并计数。a20c5769 之后改成先写临时文件再改名，并支持 `touch <out>/seg_<start>/STOP` 干净停止。

### 停下之后自动做的事

`ops/2026-09-29/post_stop.py` 在内核里排在回放后面执行，依次：
1. 确认回放进程都已退出，停掉每小时一次的云端硬盘同步。
2. 从每个库里拷出 `breakout_t1_current`，放到 `/content/db_t1/full/seg_*/<配置>.sqlite`，给 `evaluate.py --db-dir` 用。
3. 同步到云端硬盘。
4. 用 a20c5769 的代码跑评估，输出到 `/content/eval/full_partial`。
5. 按段列出完成的交易日，写入 `eval/completed_days_by_segment.json`。
6. 打一个小包 `radar_eval_small.tar.gz`，再同步一次。
7. 给被杀的段导出研究数据包（事件、影子记录、状态迁移、事件头、T1），放在 `bundles_killed/`，限时进行；导出后删掉对应的库。在 seg_2026-09-24 上核对过，导出的包与运行器自己写的逐字节相同。
8. 再同步一次，然后卸载云端硬盘。

## 5. 数据在哪（用户的 Google 云端硬盘，`option-pro-data/`）

| 位置 | 内容 |
|---|---|
| `replay_archive_2026-09-28/replay_bundle.tar.gz` | 日线缓存库 `replay.sqlite`、点时目录 `massive_directory_2026-09-27`、行业表、FRED 数据；sha256 9e20ac81…，SHA256SUMS 在同目录 |
| `massive_minute_frozen_2026-09-28/` | 5 分钟 K 线冻结包 `minute_{smoke,2021..2026}.tar`，各带 `.tar.sha256` |
| `radar_replay_2026-09-29/inputs/` | 点时股数 `pit_shares.jsonl.gz`、SPY 分钟线 `minute_spy.tar`、全量清单 `minute_manifest.jsonl`（含 SPY）、九月生产导出、烟雾子库、FRED、普查与名单；有 SHA256SUMS |
| `radar_replay_2026-09-29/full/seg_<start>/<配置>/ledger/<日>.jsonl.gz` | 回放账本，每段、每个配置、每天一个文件 |
| `radar_replay_2026-09-29/db_t1/full/` | 各段各配置的 T1 精简库 |
| `radar_replay_2026-09-29/bundles_killed/full/` | 被杀段的研究数据包（导出到哪算哪，见 `logs/export_bundles.json`） |
| `radar_replay_2026-09-29/eval/` | 评估结果 `full_partial/`（result_pack.json、README_tables.md、decision.json、逐触发明细）和 `completed_days_by_segment.json` |
| `radar_replay_2026-09-29/logs/` | 运行、守护、收尾、评估、导出的日志 |

原始行情和生产导出只放在云端硬盘，**不要提交进仓库**（仓库是公开的）。仓库里只放汇总表。

## 6. 怎么复现或接着跑

### Colab 命令行

- 本机已装 `~/.local/bin/colab`（0.7.4），每条命令都写 `--auth=oauth2`。
- G4 约 8.9 计算单元/小时，大内存 CPU 机器约 0.26 计算单元/小时。
- 挂载云端硬盘（`colab --auth=oauth2 drivemount -s <会话>`）要用户本人在终端面板里点授权。

几个坑：
- 长任务必须用 `colab exec -f 脚本` 占住内核，本地放后台，并加 `--timeout 43200` 或更大。内核空闲的后台进程约 2 小时后会被回收，2026-09-28 因此丢过 31 GB 数据。
- 查状态用 `colab console`；传文件用 `colab upload`，单个文件超过约 78 MB 要切块。
- `exec` 的本地超时只是客户端不再等待，远程照样跑完。
- 被杀的进程若会被编排脚本重试，先把入口脚本改名。

### 搭环境

参照 `ops/2026-09-29/g4_setup.py`：
1. 用 `uv venv --python 3.12.13`，再按 `backend/requirements-ci.txt` 装依赖，外加 `pyarrow==25.0.1`。
2. 从云端硬盘还原 `replay_bundle.tar.gz` 和分钟线 tar，核对 sha256。
3. 用 `scripts/build_minute_store.py` 建分钟库（约 1 小时，14 GB）。SPY 要单独加进清单，见 `g4_setup3.py`。

### 只重跑评估

不需要 G4，大内存 CPU 机器就够。
1. 还原 `replay.sqlite`、目录、`full/`、`db_t1/`。
2. 执行：

```
python research/breakout_radar/replay_v1/scripts/evaluate.py --db /content/data/replay.sqlite \
  --replay '/content/replay/full/seg_*' --variants baseline,confirm3,chase15,orb15,orb60,disc5,adv25,basemin15 \
  --baseline baseline --directory /content/data/massive_directory_2026-09-27 \
  --db-dir /content/db_t1/full --workers 8 --out /content/eval/full_partial
```

### 补跑没覆盖的天

- 每段只跑了前面一部分，后面的天没跑。
- 补跑时按子段处理：从该段最后完成日的下一天开始，`--warmup 1`，也就是把最后完成日当预热重跑一遍。输出放到新目录 `seg_<新起点>`。
- 这相当于增加了段边界。这一点和磁盘预算都要**先写成预登记修订 6，再看结果**。
- 磁盘预算：每「段·天」0.28 GB。
- 并行度按 G4 的 48 个虚拟核、176 GB 内存定。
- 编排用 `scripts/run_segments.py`。它对「被信号杀掉」的进程也会重试，用前把这一点改掉：返回码小于 0 时不重试。

## 7. 待办（按顺序）

1. **补修订 5 的事实附录。** 把每段实际完成的交易日清单写进 PREREGISTRATION.md 修订 5 的末尾，来源是 `eval/completed_days_by_segment.json`。评估用的正是这份清单。
2. **按预登记第 9 节的规则判定采纳。** 结果在 `decision.json`。
   - 年份规则只计配对天数达到 60 的年份（修订 5）。
   - 被采纳的配置要在五年上单独再跑一遍作确认，这一轮没做。
3. **补跑剩余交易日**，做法见第 6 节。
4. **「实时数据」敏感性运行这一轮没跑**，对应 `baseline,rvol2,lookback10`，K 线延迟和 TradingView 延迟都设为 0（修订 3）。两个相对量候选暂无结论。
5. **独立审查**生产提交 85371bcc、20b6d3f0 和 `harness/` 的评估代码。
6. **提 PR 并附回测数据。** 部署到生产要用户确认；部署后核对 `config_hash`，见第 2 节。
7. **选股的两件小事**：版本标签、前端拒绝理由。

## 8. 规矩

- **Massive 密钥**：只在本机 `~/.config/option-pro/massive.env`（权限 600）。上传到机器时放 `/content/.massive.env`，只放在 `Authorization` 请求头里发送。不打印、不写进文件、不提交。
- **授权**：OAuth 登录（包括云端硬盘挂载）由用户本人操作。
- **要先问用户的事**：生产主机上的部署和数据操作、推送到共享分支、删除不可恢复的数据、花钱的操作（包括开 G4）。
- **生产服务器**：上面不用 `git checkout --force`。
- **云端硬盘的研究存档**：用户没明确要求就不删。
- **回复用户**：用通俗中文，句子短，不用推销式套话（见用户的 CLAUDE.md）。

## 9. 本轮结果

（运行结束后补：停止原因与时间、每年和 P1、P2 覆盖的天数、主指标表、各配置的判定。）
