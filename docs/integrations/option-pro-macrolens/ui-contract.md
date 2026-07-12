# Night Desk 界面契约

## 生产文件

入口为 frontend/index.html、optix-deck.css、deck-api.js 和 deck-app.js。新增 deck-catalysts.js、deck-ai-jobs.js 和 optix-catalysts.css。旧 pages 目录和 v3 样式只作归档，不承载新生产功能。

## 导航和路由

桌面主导航新增 06 催化剂，路由为 #catalysts。支持 ticker、classification、tab 和 news 查询参数。手机底部保留现有五项，Catalyst 放入移动菜单，并显示当前页状态。

## 页面结构

1. 状态页眉：连接、截止时间、同步时间、来源、Terra/max 和免责声明。
2. 六项摘要：缺失值显示 —。
3. 新闻流、股票影响、经济日历、数据源四个页签。
4. draftFilters 与 appliedFilters 分离的筛选区。
5. 新闻时间线、影响表、日历和来源健康内容区。

未分析新闻不显示方向、影响分或置信度。股票影响排序明确标记为展示排序，不改变正式评分。

数据源页显示状态、最后尝试、最后成功、下一次尝试以及最近一轮 raw、inserted、duplicates。disabled、not_configured 和未尝试值使用中性状态与“—”，不显示为 0。

## 分析抽屉

抽屉展示原始新闻、已有分析、模型、推理等级、时间、因果摘要、关键因素、不确定性和股票影响。请求分析必须由用户点击。

任务轮询间隔为 2、3、5、8、10 秒；页面隐藏时降频，离开路由时停止。完成后只更新分析区，保持抽屉滚动和焦点。

## 上下文接入

- Breakout：最近三条新闻，失败不隐藏技术证据。
- Screener：使用批量本地 API，默认排序仍是 ranking_score。
- Stock Drawer：72 小时时间线，失败不影响行情、K线、期权、估值和评分。
- Earnings：独立按钮创建 AI Job，选择行不自动收费。

## 安全与无障碍

新闻按纯文本转义；外链只允许 http/https，并带 target=_blank、rel=noopener noreferrer。抽屉有焦点锁、背景 inert、Esc 关闭和焦点恢复。页签支持方向键、Home 和 End；aria-current、focus-visible、reduced-motion、双主题和长标题换行必须完整。

## 响应式验收

检查 1440×900、1280×800、1024×768 和 390×844，深浅主题及 active、empty、degraded、stale、unavailable、queued、in_progress、completed、failed。不得出现横向页面滚动、被裁阴影、拥挤 Dock、假数据或假进度。
