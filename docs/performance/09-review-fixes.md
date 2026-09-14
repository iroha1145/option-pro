# 审查修复（PR #162 R1–R4）

审查基准 head：`23cfbfa08c5e1136f54523d59947aef9b5c50c9e`。
本轮是修复与复验，不是重新开一轮无限优化。

## R1 预取走有界传输

`requestRaw` 命中预取后不再只等 Response 头。消费路径与普通请求共用 `bufferExistingResponse`：正文截止时间、调用方取消、32 MiB 上限。预取失败回退到 `fetchBuffered`。不能靠放宽超时或体积上限交差。

## R2 预取袋

袋内条目带 `createdAt`、主体世代、容量 8、TTL 30s（与催化读缓存一致）。`clearCatalystReadCache` 调用 `invalidateBootPrefetch()`：登录、注册、登出、会话失效、身份变化、手动刷新与业务作废一起换代。旧请求即使忽略 abort 晚到，也不能再被新世代消费。失败槽会删掉，避免长期保存拒绝。

## R3 抽屉任务恢复

恢复 effect 依赖 `analysisJobId` / `analysisStatus`，不再只看 `newsId`。seed 无任务、详情稍后带回 queued/in_progress 会开始轮询。关抽屉或换条用 `pollGenRef` 丢弃迟到响应。详情瞬时失败按 1.5s/3s 重试。行为测试在 `frontend-src/tests/news-drawer-recovery.test.mjs`。

## R4 测速 ready 语义

`scripts/perf/lib/page_ready.mjs` 把页面分成 pending / shell / empty / error / content。`measure_pages.mjs` 保留全部样本，分别报告 content/empty/error/timeout 比率与分位。`/screener` 标题加任意按钮只算 shell；`/stock/NVDA` 的服务异常、429、登录失效算 error，不进 content 耗时。

## 修复后复测（`64f23ad5` 起，mobile-ref）

新闻交错 n=8，标题均为 `第9600条快讯`，0 次 429：

| 侧 | 冷 ready p75 | 热 ready p75 |
|---|---|---|
| 优化 | 1714 | 838 |
| 未优化 | 2434 | 1100 |

交互 n=8：抽屉 305 / 筛选 243，预取 8/8。与修复前 n=20 批次同量级，不是同一批，不能混成一张总表。

新语义非新闻页 n=4：

| 路由 | 类别 | 说明 |
|---|---|---|
| `/` | content 4/4，p75 2255 | 首页指数区 |
| `/watchlist` | empty 4/4 | 本实验室账号 0 只自选，合法空，不是失败 |
| `/screener` | empty 4/4 | 未扫描空态；标题加按钮只算 shell |
| `/stock/NVDA` | content 4/4，p75 6547 | 真实个股内容；错误文案不进该分布 |

原始 JSON：`/opt/cursor/artifacts/perf/review-interleaved-n8.json`、`review-interact-n8.json`、`review-pages-*.json`。

## 明确仍不是

- 模拟器 / Playwright Chromium 不是真机 Safari
- 本机 4 核不是 16 核容量
- CI 双容器通过不是本机 Docker 压测
- 不宣称所有模块已无优化空间
- 本轮交错是 n=8 复验，不是再宣布一组 n=20 总成绩
