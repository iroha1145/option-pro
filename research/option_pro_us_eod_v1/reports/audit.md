# 阶段 0 审计（基线 `8b620d760f05020d2c1b51a3c3228a89fd9f3120`）

核对日期：2026-09-16。`origin/main` 与该基线相同，没有需要保留的后基线生产改动。

本文件记录**代码事实**与研究改造边界。它不是收益结论。

## 可直接复用

- `backend/app/services/strength/features.py`：RSI / MACD 变化 / ATR% / 均线 / 收益 / `compute_price_action` / `compute_vol_price_match`。详情与选股已共用这一层。
- `backend/app/services/strength/scoring.py`：缺失不填中性分、有效权重重配。`price_action` 已是独立因子，又在 breakout 子项里再用——新方案不能把详情总分整块加回。
- `backend/app/services/strength/scanner.py::_complete_daily_frame`：按美国常规时段收盘截断未完成日线。`_scan_sync` 仍读取 `datetime.now` 并下载全池日线、可选期权链。
- `backend/app/services/technical/base_structure.py`：纯数据平台检测，调用方必须排除评价日。窗口网格 `{10,15,20,30,40,60,80}` 可复用。
- `backend/app/services/market_calendar.py`：`America/New_York`、假期、7/3 与感恩节次日等半日市。
- `backend/app/services/algorithm_modes.py`：生产默认 `production`，A0 与 T1 独立。研究算法**没有**加入 `SCREENER_ALGORITHMS` / `RADAR_ALGORITHMS`。

## 必须重构（研究轨道，不改默认）

- `_scan_sync` 不是 `compute_snapshot(as_of, historical_data, universe_version, config)`。回测禁止拨系统时钟。
- 盘中扫描仍可能触发行情/期权路径。EOD 模式必须在整条调用图上禁止刷新，而不是只藏按钮。
- `SECTORS` 24 个主题重叠；`primary_sector_id` 为「首次出现优先」，不是经济行业。
- 美股场所目前用 `"." in symbol` 排除（`sectors.py` / `_theme_universe`）。奢侈品 `RMS.PA` 碰巧被点号规则丢掉，但 `LVMUY`/`CFRUY` 无点号却是 OTC。研究层改为显式 MIC/交易所/证券类型。
- `price_action.py` 默认 `swing_span=3`，陷阱过滤写死 `idx < i - 1`，没有 `pivot_at` / `confirmed_at`。研究层按 d+span 确认。

## 仅显示、不可直接打分

- 详情页图表渲染、新闻、LLM、期权热度、突破雷达 T1 日优先级。
- 量价「努力/结果」「吸收」「真空」解释字段：首轮不叠加进 V。
- 当前 ETF 入口把宽基、行业、主题、黄金、长债放在同一列表。研究必须拆五个子资产。

## 当前关注池缺口（U_current 静态，不是 PIT）

- 跨主题：至少 `NVDA`（半导体 + AI/云）以及文档已记录的其他重复标签。
- ETF 混装：`GLD`、`TLT` 与 `SPY`/`QQQ` 同入口。
- 非美股：`RMS.PA`。
- OTC ADR：`LVMUY`、`CFRUY`。
- 无退市样本、无代码复用史、无历史主题有效期。`ai_cloud` 等新主题不能回填十年标签。

## 数据合同

本环境未核验 Massive/Norgate/其它许可。Massive MCP 当时不可用。因此：

- U_PIT：`INSUFFICIENT_PIT_HISTORY`
- 864/180/12 市场行：`DATA_INSUFFICIENT`
- 不得填写收益数字

## 生产不变性

- 未修改 A0、T1、日股、订单、期权或账户权限逻辑。
- `RESEARCH_EOD_V1_ENABLED` 默认 `false`。
- 新路由挂在 owner 边界，刷新在无许可数据时拒绝拉行情。
