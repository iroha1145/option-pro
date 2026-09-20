# LIMITED_CURRENT_UNIVERSE_V1.1

这是同一份真实 Yahoo 缓存上的接线收尾，不是新一轮研究。旧结果仍在 `../limited_current_universe_v1/`。`evidence_status=UNVALIDATED_LIMITED_DATA`。空综合名单是合法结果。

## 未重抓、旧结果保留

- 缓存文件未改：`data/cache/limited_current_universe_v1/bars.pkl`（gitignored，63469417 字节）
- 文件 SHA-256 仍是 `cc26ef2ecb35a24a852720a2f8b6a3bbea238b83549494019f1eb34083efc622`
- 本环境 `fetched=0`，评分期 Yahoo 批次 0
- 当前名单 214；有日线 213；`CRWV` 仍缺失
- 旧包 `limited_current_universe_v1` 未原地改写

计算摘要变了，是因为 v1.1 绑定了实际消费的 Close 与质量字段，不是因为重新下载：

- 旧 OHLCV `dataset_hash=a8541164c3a7b4bf1d8a272f2d853ecb1d198743fa5245c289609b47193d1c7f`
- 新计算摘要 `058c56a4587fd4b5add589fd3cce1279b31e73571609713d287ca4fd3ca68a76`

本地导入：把同一 `bars.pkl` 放回上述 gitignored 路径。没有原文件时，只能按锁定的 `yfinance==1.5.1` 参数重下；只提交 hash 不能恢复行情。

## 为什么分数和资格变了

| 接线 | v1 | v1.1 |
| --- | --- | --- |
| 行业字段 | `themes[0]` 写入 `industry_id` | 无独立分类则为 None |
| 回报轨 | 标签写价格收益，M/D 仍读供应商 TRI | `tri=Close`，供应商 Adj Close 另存 |
| 缺 G 的评分拒绝 | 上游 rejected 会留下 | 受限轨重算；硬拒绝保留 |
| 未证实美元流动性 | A/D 可继续 eligible | 四家族一律 watch |
| M1 | 股票与 ETF 混在一次共识 | 分轨；本轮两轨皆空 |

对照表：`eligibility_delta_vs_v1.json`。

## 成功跑通的命令

```bash
PYTHONPATH=backend python research/option_pro_us_eod_v1/scripts/run_limited_current_universe_v1.py \
  --dataset yahoo_cache --session 2024-06-28 --replay-days 20 --profile balanced --horizon mid \
  --no-network --smoke-864 \
  --out-dir research/option_pro_us_eod_v1/return_pack/limited_current_universe_v1_1
```

`--dataset synthetic` 走合成夹具，不会给真实缓存换标签。

## 运行身份

| 项 | 值 |
| --- | --- |
| 源码 SHA（评分时） | `48f64e7f` |
| 模式 | `LIMITED_CURRENT_UNIVERSE_V1` |
| 计算版本 | `limited-current-v1.1` |
| 宇宙版本 | `u_limited_current_v1_1` |
| 证据级别 | `UNVALIDATED_LIMITED_DATA` |
| 成员口径 | `CURRENT_MEMBERSHIP` |
| 能力轨 | `PRICE_ONLY_DIAGNOSTIC` |
| 回报轨 | `close_price_return` |
| 量能 | `VENDOR_DAILY_UNVERIFIED` |
| 配置哈希 | `9e5c8559a006ad455a0569fb7f856f9a23841cd5b348e8cead70ad584a9606bf` |
| 开发区截止 | `2024-06-28` |
| 留出区 | `2024-07-01` 起仍封存 |

机器可读清单：`checklist.json`。

## 2024-06-28 主路径

- 状态 `RAN`；第一层合格 0；观察 25 行 / 18 只去重证券；股票综合 0；ETF 综合 0
- 快照 920 行 / 213 只去重证券。四个家族各 230 行（主题出现次数，不是去重证券）
- 观察原因是 `DOLLAR_LIQUIDITY_UNVERIFIED`。A 观察 10 行，D 观察 15 行，B/C 观察 0
- 旧口径同一日合格 19、M1=`COST`。本轮不把观察包装成合格 M1
- 预览有主题家族明细、股票/ETF 分表，无 SYNTHETIC 标

## 20 日回放

选择规则：开发区 `<= 2024-06-28` 的最后 20 个官方交易日，不按表现挑选。

| 日期 | 合格 | 观察行 | M1 |
| --- | ---: | ---: | --- |
| 2024-05-31 | 0 | 29 | （空） |
| 2024-06-03 | 0 | 29 | （空） |
| 2024-06-04 | 0 | 27 | （空） |
| 2024-06-05 | 0 | 24 | （空） |
| 2024-06-06 | 0 | 23 | （空） |
| 2024-06-07 | 0 | 21 | （空） |
| 2024-06-10 | 0 | 21 | （空） |
| 2024-06-11 | 0 | 21 | （空） |
| 2024-06-12 | 0 | 21 | （空） |
| 2024-06-13 | 0 | 20 | （空） |
| 2024-06-14 | 0 | 19 | （空） |
| 2024-06-17 | 0 | 18 | （空） |
| 2024-06-18 | 0 | 18 | （空） |
| 2024-06-20 | 0 | 18 | （空） |
| 2024-06-21 | 0 | 21 | （空） |
| 2024-06-24 | 0 | 15 | （空） |
| 2024-06-25 | 0 | 13 | （空） |
| 2024-06-26 | 0 | 12 | （空） |
| 2024-06-27 | 0 | 17 | （空） |
| 2024-06-28 | 0 | 25 | （空） |

- 回放 989.584s；含 864 烟测共 1375.631s
- 峰值内存 692.8MB
- 评分/预览期间 Yahoo 批次数 0
- 日期顺序与官方日历一致
- 离线重算 2024-06-28：96 个家族指纹、`config_hash`、`run_signature` 一致

## 864 烟测

全部 864 行 `status=RAN`。RAN 只证明入口可跑，不是回测或有效策略认证。各档合格合计都是 0，因为美元流动性未证实。

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

本环境：上述集合 153 passed。CI 用合成 fixture，不需要 `bars.pkl`。

生产 `/api/research/eod/v1/refresh` 仍返回 409。本 CLI 不挂回生产路由。

## 剩余限制

- 当前名单，不是历史成员或退市并集
- Yahoo Close 不能证明是历史未复权成交价
- 日成交量时段未核实；美元/股份口径未证实，故无合格 M1
- 动量与标签是价格收益，不是总回报
- 执行、美元风险、公司行动账本未核实
- 正式十年验证条不降低；付费 Sharadar 未买

完整数据迁移仍看 `../limited_current_universe_v1/FULL_DATA_MIGRATION.md`。不要把本版结果倒写成 PIT 通过。未开权重搜索，未追付费 SKU。
