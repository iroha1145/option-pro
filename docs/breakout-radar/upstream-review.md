# BreakoutAnalysis 上游审查

## 固定来源

- 仓库：https://github.com/calesthio/BreakoutAnalysis
- 审查提交：4e5619ac2a90958217d3d356da7528a96df9c000
- 远端 main 与 HEAD 在 2026-07-12 均指向该提交。
- 许可证：麻省理工学院许可证（MIT License）。

审查覆盖 LICENSE、README、配置、依赖、regular/premarket/postmarket
screener、Alpaca 历史过滤、主循环、本地 JSON、新闻、LLM、截图、
Discord、Gmail 和 OAuth。

## 采用的概念

- 固定 TradingView America 扫描域名和路径。
- regular 与 premarket 分开构造粗筛请求。
- 涨幅、成交额、相对量、市值和价格构成轻量发现层。
- 限制候选数、检测返回列长度和候选去重。
- Provider 只是可替换发现端口。

采用方式是独立重写，不复制完整模块，不引入上游依赖集合。

## 明确拒绝

- Git 子模块、requests、Alpaca 历史筛选和本地 JSON 状态。
- 100 美元价格上限与科技行业市值豁免。
- 仅按成交股数判断流动性。
- Provider RSI、MACD、均线、VWAP 和 Pivot 进入正式评分。
- 数据失败时自动通过质量过滤。
- Playwright、Selenium、截图、Cookie 和登录态。
- 新闻、Torch、Transformers、Ollama、Dash、Flask。
- Discord、Gmail 和 OAuth。
- FastAPI 进程内永久循环与固定 sleep(900)。
- LLM 生成确定性入场、止损、目标、状态或分数。

## Provider 字段与风险

上游 regular 使用 close、change、volume、relative_volume_10d_calc、
market_cap_basic、sector 等字段；premarket 另有 premarket_close、
premarket_change 和 premarket_volume。上游把 regular 相对量带入盘前结果，
它不是真实 premarket RVOL。

返回行依赖位置数组，缺少正式 schema、时间戳、资产类型和完整度。
列变化、非数字值、空结果和错误往往都退化为空表。America 市场也可能返回
OTC 资产。因此新实现必须校验列、类型、ticker、交易所、资产类型、响应大小
和候选数量，并在漂移时返回 degraded，不做猜测。

## 许可处理

完整上游 MIT 文本保存在 third_party/BreakoutAnalysis-LICENSE。
THIRD_PARTY_NOTICES.md 记录仓库、提交、改写概念与拒绝组件。容器镜像也必须
携带这两份文件。

MIT 许可证不代表 TradingView 对未公开接口、数据、Cookie 或商标的授权。
TradingView Provider 被视为可替换、非官方、可能漂移的数据发现来源。
