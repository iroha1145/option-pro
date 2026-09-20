# LIMITED_CURRENT_UNIVERSE_V1

受限工程版：让现有研究函数在**当前主题名单**的真实日线上跑通。这不是十年 PIT 认证，也不是参数寻优。`evidence_status=UNVALIDATED_LIMITED_DATA`。

主路径：`24 主题 × A/B/C/D × balanced × mid` → M1 共识 → 原子快照 → 本地只读 HTML。

## 成功跑通的命令

在仓库根目录、已有本地 Yahoo 缓存时（本环境实际执行成功）：

```bash
PYTHONPATH=backend python research/option_pro_us_eod_v1/scripts/run_limited_current_universe_v1.py \
  --dataset yahoo_cache --session 2024-06-28 --replay-days 20 --profile balanced --horizon mid \
  --no-network --smoke-864 \
  --out-dir research/option_pro_us_eod_v1/return_pack/limited_current_universe_v1
```

本环境等价调用：`load_or_fetch_bars(allow_network=False)` 后把 panel 交给 `run_limited_v1(..., replay_days=20, smoke=True)`。

首次补齐缓存（仅填当前名单缺口；锁定 `yfinance==1.5.1`）：

```bash
PYTHONPATH=backend python research/option_pro_us_eod_v1/scripts/run_limited_current_universe_v1.py \
  --dataset auto --session 2024-06-28 --replay-days 1 --allow-network \
  --out-dir research/option_pro_us_eod_v1/return_pack/limited_current_universe_v1
```

只读预览，不发供应商请求：

```bash
PYTHONPATH=backend python research/option_pro_us_eod_v1/scripts/run_limited_current_universe_v1.py --preview-only
```

用浏览器打开 `preview.html`。标题是「历史 EOD 预览」，不是今日选股。页面不展示 IC / CAGR / 回测账本。

## 运行身份

| 项 | 值 |
| --- | --- |
| 源码 SHA（评分时） | `7feadcbaf2b356d517aa0690038291fb38a623e9` |
| 模式 | `LIMITED_CURRENT_UNIVERSE_V1` |
| 证据级别 | `UNVALIDATED_LIMITED_DATA` |
| 成员口径 | `CURRENT_MEMBERSHIP` |
| 能力轨 | `PRICE_ONLY_DIAGNOSTIC` |
| 量能 | `VENDOR_DAILY_UNVERIFIED` |
| 动量 | `price_return_not_total_return` |
| 开发区截止 | `2024-06-28` |
| 留出区 | `2024-07-01` 起仍封存 |
| 配置哈希 | `70fdb339e822146a78464ac7c6e2f48151bcc02d6e804bc265c8b1453f2a8e28` |

机器可读清单：`checklist.json`。数据集：`dataset_manifest.json`。

## 数据

1. `research/option_pro_us_eod_v1/data/cache/round3_yahoo_abcd/bars.pkl`：**不存在**
2. 本轮缓存：`data/cache/limited_current_universe_v1/bars.pkl`（约 61MB，**gitignored**）
3. 内容哈希 `dataset_hash=a8541164c3a7b4bf1d8a272f2d853ecb1d198743fa5245c289609b47193d1c7f`
4. 文件 SHA-256 `cc26ef2ecb35a24a…` 见 manifest
5. 当前名单 214；有日线 213；`CRWV` 在窗口内 Yahoo 空下载（IPO 晚于 2024-06-28），保留为 missing
6. Sharadar Sample 只作适配器对照，不是本版主源
7. 不依赖 Massive

复现需要同一份 `bars.pkl`（或按锁定参数重新下载）。只提交 hash 不能恢复行情。

## 2024-06-28 主路径

- 状态 `RAN`；第一层合格 19；观察 0；M1 综合 1：`COST`（A+D，共识分 80.5957）
- 24 个主题都有结果。参考池约 203 只同轨证券，未在评分前裁掉非主题同行
- 快照 920 行带真实 T/M/S/B/P/V/R 因子，**没有** `AUTH_REQUIRED` 占位
- B/C 因量能未核实，合格项会降为观察；该日 B/C 合格为 0
- 空综合日保留空名单：`2024-06-11`、`2024-06-12`

主题明细：`theme_m1_2024-06-28.json`。快照：`research-eod-v1-snapshot.json`。

## 20 日回放

选择规则：开发区 `<= 2024-06-28` 的最后 20 个官方交易日，不按表现挑选。

| 日期 | 合格 | 观察 | M1 |
| --- | ---: | ---: | --- |
| 2024-05-31 | 12 | 0 | GS |
| 2024-06-03 | 12 | 0 | GS |
| 2024-06-04 | 13 | 0 | GS, GOOGL |
| 2024-06-05 | 10 | 0 | GS |
| 2024-06-06 | 9 | 0 | GS |
| 2024-06-07 | 12 | 0 | GS |
| 2024-06-10 | 11 | 0 | GS |
| 2024-06-11 | 9 | 0 | （空） |
| 2024-06-12 | 11 | 0 | （空） |
| 2024-06-13 | 13 | 0 | WELL, VRTX, SO |
| 2024-06-14 | 10 | 1 | WELL, HEI |
| 2024-06-17 | 12 | 0 | HEI, WMT |
| 2024-06-18 | 10 | 1 | HEI |
| 2024-06-20 | 12 | 2 | GOOGL, WMT |
| 2024-06-21 | 20 | 0 | COST, TMUS, GOOGL |
| 2024-06-24 | 13 | 0 | COST, TMUS |
| 2024-06-25 | 11 | 0 | COST |
| 2024-06-26 | 10 | 0 | COST |
| 2024-06-27 | 11 | 0 | COST |
| 2024-06-28 | 19 | 0 | COST |

- 回放耗时 1025.531s；含 864 烟测共 1412.76s
- 峰值内存 692.6MB
- 评分/预览期间 Yahoo 批次数 0，预览供应商调用 0
- 日期顺序与官方日历一致
- 离线重算 2024-06-28：指纹、`config_hash`、`run_signature` 与回放当日一致

## 864 烟测（单日 3 档 × 3 周期 × 24 × 4）

全部 864 行 `status=RAN`，无“只改标签、不改特征”的假周期。

| profile | horizon | 配置数 | 合格合计 | RAN |
| --- | --- | ---: | ---: | ---: |
| conservative | short | 96 | 2 | 96 |
| balanced | short | 96 | 16 | 96 |
| aggressive | short | 96 | 38 | 96 |
| conservative | mid | 96 | 3 | 96 |
| balanced | mid | 96 | 19 | 96 |
| aggressive | mid | 96 | 37 | 96 |
| conservative | long | 96 | 7 | 96 |
| balanced | long | 96 | 20 | 96 |
| aggressive | long | 96 | 42 | 96 |

明细：`smoke_864.json`。数字只证明入口可跑，不用于选优。

## 测试

```bash
PYTHONPATH=backend python -m pytest \
  tests/test_research_eod_v1_limited_v1.py \
  tests/test_research_eod_v1_lookahead.py \
  tests/test_research_eod_v1_composite.py \
  tests/test_research_eod_v1_eod_shadow.py \
  tests/test_research_eod_v1_pr176_boundaries.py \
  tests/test_research_eod_v1_connect_accept.py \
  tests/test_research_eod_v1_yahoo_snapshot.py \
  tests/test_research_eod_v1_production_boundary.py \
  tests/test_research_eod_v1_network_spy.py \
  tests/test_research_eod_v1_correctness.py \
  tests/test_research_eod_v1_algorithms.py \
  tests/test_research_eod_v1_features.py \
  tests/test_research_eod_v1_sharadar.py \
  tests/test_research_eod_v1_sharadar_fixes.py \
  tests/test_research_eod_v1_finish_gate.py -q
```

本环境：上述集合 159 passed。CI 用合成 fixture，不需要 `bars.pkl`。

生产 `/api/research/eod/v1/refresh` 仍返回 409 `DISABLED` / `DATA_INSUFFICIENT`。本 CLI 不挂回生产路由。

## 剩余数据限制

- 当前名单，不是历史成员或退市并集
- Yahoo `auto_adjust=False` 不能证明 Close 是历史未复权成交价
- 日成交量时段未核实，不能当成交额/容量/执行成本
- 动量与标签是价格收益，不是总回报
- 执行、美元风险、公司行动账本未核实
- G 走价格诊断轨；D 是市场残差诊断
- 正式十年验证条不降低；付费 Sharadar 未买

获得完整数据后的迁移：`FULL_DATA_MIGRATION.md`。不要把本版结果倒写成 PIT 通过。
