# 实现地图

对照文档与代码。页面名不等于模块名。

## 选股

| 环节 | 路径 | 说明 |
|------|------|------|
| 页面 | `frontend-src/src/pages/Screener.tsx` | 路由 `/screener`，导航「选股」 |
| API | `GET /api/strength/scan` | 只读 worker 快照，不在 GET 时全市场实扫 |
| 刷新 | `POST /api/worker/actions/strength_refresh` | 所有者触发 |
| 扫描 | `backend/app/services/strength/scanner.py` `_scan_sync` | 全主题池特征 → 内在分 → 百分位 → 环境/档案 → 视图过滤 |
| 特征 | `backend/app/services/strength/features.py` `_feature_row` | strength-features-v3 |
| 评分 | `backend/app/services/strength/scoring.py` | strength-v3；缺失不补 50 |
| 股票池 | `backend/app/services/sectors.py` `SECTORS` | 仅 `themes`，约 200+ 去重代码 |
| 持久化 | `/data/strength-snapshot-v1.json` | 无 SQL 扫描历史，无 `published_at` 审计链 |
| 点时入口 | `score_ticker_set(as_of=...)` / `score_ticker_frames` | 内在分；现已向 `_scan_sync` 注入 `as_of` + `raw_history` + `enrich_live` |

文档 `docs/strength-scoring-v2.md` 与代码一致处：intrinsic / market_fit / profile_fit 分离、先全池后筛选、期权不进内在分、RP 默认 shadow。

不一致或缺口：

- 选股没有 `raw_as_of` / `feature_cutoff_at` / `published_at` 字段；新鲜度用 `as_of` 与 `daily_data_through`。
- `_scan_sync` 原先把 `as_of` 写成扫描结束墙钟。现改为观察时钟 `observed_at`；默认仍是现在，生产路径不变。
- 无历史股票池、无退市成员、无选股快照时间序列，因此不能宣称全市场发现能力。

## 突破雷达

| 环节 | 路径 | 说明 |
|------|------|------|
| 页面 | `frontend-src/src/pages/Breakouts.tsx` | 路由 `/breakouts` |
| API | `backend/app/api/breakouts.py` | 只读 completed 快照 |
| Worker | `backend/app/services/breakouts/worker.py` | Discovery → 日线 60 → 盘中 30 → 发布 → 延续 |
| 结构 | `base_detector.detect_base` | 枢轴右侧确认；已越过阻力的窗口丢弃 |
| 触发 | `breakout_detector.detect_breakout` | 完成 K 线；Discovery 价不触发 |
| 研究导出 | `breakouts/research.py` | 只读 completed + published_at |
| 标签/滚动 | `breakouts/research_validation.py` | 1/5/20/63；触发价是标记不是成交 |

`docs/breakout-radar/research-validation.md` 与代码一致：点时序、离线价格、不补零、Model B 不可用、决策永远不足以上线。

缺口：

- 本 VM 无生产库，Grade A 无法执行。
- TradingView Discovery 不可历史重放。
- `compute_feature_snapshot` 无盘中 K 线即 `insufficient_data`，故 ORB / 盘前缺口在本任务中标记不可完整验证。
- 日线重建把结构截止在 T-1，避免突破 K 线生成自身阻力；这是反事实重建，不等于当年盘中扫描。

## 研究执行层

| 模块 | 作用 |
|------|------|
| `app/services/research/protocol.py` | 冻结区间、主指标、试验预算、封存禁看 |
| `app/services/research/dataset.py` | 离线 OHLCV，回放不联网 |
| `app/services/research/screener.py` | 调用生产 `_scan_sync` |
| `app/services/research/radar.py` | 调用生产 `detect_base` / `detect_breakout` |
| `app/services/research/labels.py` | 标签分表；缺会话不顺延 |
| `app/services/research/portfolio.py` | 下一开盘成交、资金守恒 |
| `app/services/research/cli.py` | 分命令：audit / replay / portfolio；按日落盘并可 resume |
| `app/services/research/replay_store.py` | 部分结果 JSONL |
| `scripts/research/analyze_screener_modes.py` | 同一份 dump 报告六种模式与两项候选 |
| `scripts/research/analyze_combo.py` | 只用更早选股快照做联用 |
