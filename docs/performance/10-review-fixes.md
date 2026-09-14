# 审查修复（PR #162 续：抽屉恢复 / 详情重试 / ready 业务区）

审查基准 head：`348c58b15909b3c917ce8aede66f7c0b0008436a`。
本轮只修仍存在的问题。已完成的预取有界传输、30s TTL / 容量 8 / 世代隔离与分桶报告保持不动。

## 1. NewsDrawer 任务恢复

恢复 effect 使用独立 `recoverySeqRef` + `disposed` 清理。`analysisJobId` / 状态变化会作废上一轮，不再只靠关抽屉才递增的 `pollGenRef`。返回时核验 `newsId` / `jobId` / 会话世代。

迟到的旧任务 A 不得替换已经在跟的新任务 B（`shouldApplyRecoveryJob`）。首次 `analysisJob` 503 对同一任务 ID 有界退避，不 POST 创建。首次恢复已是 `completed` 时，详情刷新失败会留下「详情更新失败 / 重试」。

## 2. 两条详情读取重试

初次 `news()` 与终态 `refreshItem` 共用 `runBoundedRead`：

- 429/503：等待 `max(本地 1.5s/3s, Retry-After)`
- 401/403/404 与 `retryable=false` 立刻停；401 仍走既有主体失效
- effect 清理取消睡眠；每次网络调用前检查关闭、换新闻、会话世代
- 有 seed 时保留标题摘要，显示轻量「详情更新失败 / 重试」

生产模块：`frontend-src/src/lib/boundedReadRetry.ts`。
行为测试：`frontend-src/tests/bounded-read-retry.test.mjs`、`frontend-src/tests/news-drawer-recovery.test.mjs`。

## 3. page_ready 业务区

不再用 `main.length > 40/80`、任意 table/按钮或全页「暂无」判完成。各页主业务区写入 `data-optix-region` / `data-optix-state`，状态来自真实渲染分支。

- 自选：默认卡片与表格都算内容；单只「暂无行情」仍是 content
- 选股：未扫描 = `idle`（从数据加载成绩剥离）；命中 0 只 = `empty`
- 分类器保持自包含（`Function#toString` 注入）

`measure_pages` 增加 `idle_n` / `idle_p75`；`content_p75` 不含 idle/error/timeout。

## 4. 验证

- 前端行为测试 958 pass / 0 fail（本轮新增预取/抽屉/ready 用例）
- `npm --prefix frontend-src run lint` 通过
- `VITE_API_MODE=live npm run build --prefix frontend-src` 后 `frontend/` 与 `frontend-src/dist` 一致
- 复测数字见同目录 `artifacts/`（仓库内），不要只用 Cursor 机器绝对路径

## 5. 本轮复测（`78bf552e`，mobile-ref）

实现提交 `78bf552ec2280e4236d29aab2b7154227d069f8d`。`:2000` 为新包 `index-BIAX8gp6.js`，`:2001` 仍为未优化 `index-Bnz11sk4.js`。这是单独一批，不要和 n=20 实验室（冷 1698 / 热 838）或上一轮 n=8（优化 1714/838 vs 未优化 2434/1100）混成一张总表。

新闻交错 n=8，标题均为 `第9600条快讯`，0 次 429：

| 侧 | 冷 ready p75 | 热 ready p75 |
|---|---|---|
| 优化 | 1674 | 853 |
| 未优化 | 2371 | 1081 |

冷 Δp75 −698，热 Δp75 −228。原始 JSON：`artifacts/r5-interleaved-n8.json`。

修正后判据的非新闻页 n=4：

| 路由 | 类别 | 说明 |
|---|---|---|
| `/` | content 4/4，p75 1782 | 首页指数区 |
| `/watchlist` | empty 4/4 | 本实验室账号 0 只自选，合法空，不是失败 |
| `/screener` | idle 4/4 | 未扫描；从数据加载成绩剥离，不再记 empty |
| `/stock/NVDA` | content 4/4，p75 5929 | 真实个股内容；错误文案不进该分布 |

原始 JSON：`artifacts/r5-pages-_.json`、`r5-pages-_watchlist.json`、`r5-pages-_screener.json`、`r5-pages-_stock_NVDA.json`。

## 明确仍不是

- 单元测试与实验室 Chromium 不是生产全面验证
- 模拟器不是真机 Safari
- 本机 4 核不是 16 核容量
- CI 双容器功能检查不是容量压测
