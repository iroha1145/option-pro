# 当前状态

更新：2026-09-13 最终累计回归已落盘。实验室交付齐；真机 / 日本链路 / 16 核 / Docker 仍受阻。不得把本目录写成 RUM 或生产全面验证。

## 实验室已交付

- 基线 SHA `31e8955d89dc2b9b51a5bea1c47f5cfa6ea8cabc`；分支 `cursor/perf-sitewide-1d7a`；对照 `$HOME/option-pro-unoptimized`
- 生产 `frontend/` + 单进程 uvicorn `:2000` / `:2001`；种子 n10000；回环 = Owner
- 新闻页最终 mobile-ref n=20：`browser-final-mobile-ref.json`。冷 ready p75 **1698** / 热 **838**。0 次 429，0 个 >5s 离群，20/20 标题 `第9600条快讯`
- 最终交互 n=20：`browser-final-interact.json`。抽屉 314 / 筛选 249，预取 20/20，滚动 longtask 0
- 交错对照：优化 1651/832 vs 未优化 2392/1093（冷 −31%）
- 功能回归：前端 **935 pass / 0 fail**（`final-frontend-tests.log`）；催化 pytest **228 passed**（`final-catalyst-pytest.log`）；`frontend/` 与 `frontend-src/dist` 无 diff
- 2h soak 诚实窗口 480 轮 0 错，RSS +26MB。不要用带空转的 `soak-2h.summary.json`
- `:2001` 重启 1.7s，count 与标题不变。故障五案通过
- 三轮复查：`06-review-round1.md` / `07-review-round2.md` / `08-review-round3.md`

## 关键预算（实验室 p75）

| 场景 | 结果 | 预算 |
|------|------|------|
| 新闻冷 `news_content_ready` | 1698（交错优化 1651） | ≤2500 |
| 新闻热 | 838（交错优化 832） | ≤1000 |
| 冷 LCP | 1580 | ≤2500 |
| CLS | 0.0008 | ≤0.1 |
| 筛选 24h/12 | 249 | 思考 800ms 后可用 |
| 搜索 / 分类 | 130 / 575 | 结果或合法空态可用 |
| 站内返回新闻 | 531 | 不是整页热缓存 |
| 未优化对照冷/热 | 2392 / 1093 | 热仍超 1000，说明收益来自优化而非机器变快 |

弱网档冷 4089 / 热 1371 **不适用** 上述 2.5s/1s 预算。

`summary.count` 随 72h 窗从 9566 降到约 9048（17:47Z）。这是产品时效。首条标题仍是 `第9600条快讯`。

## 明确不做（有回滚或负向证据）

- feed 整窗物化下推 SQL
- 主包拆 framer-motion
- Owner 复用匿名 revision 缓存（Round 2 已回滚）
- 放宽 heavy API 30/60s

## 回滚

工作分支上按提交回退，不要 force-push `main`，不要合并本 PR 来「撤销」。

- 只撤一轮业务改动：`git revert <sha>`（例如 Round 2 已用提交回滚 Owner 缓存）
- 放弃整分支：关闭 PR，对照树已停在基线 SHA
- 测量脚本与文档可单独 revert，不影响运行中的生产部署（本任务未部署）

## 受阻 / 部分完成

| 项 | 状态 |
|----|------|
| 真机 iPhone Safari | 未执行 |
| 日本→美国 RTT | 未执行；仅 CDP 180/300ms |
| 按生产 16 核下结论 | 本机 4 核 / 15 GiB |
| Docker 双容器 | 无 daemon；uvicorn 单进程对齐镜像 CMD |
| 访客登录表单 | 未测（Owner 已登录态 n=20） |
| ManagePanel / worker 后台专项预算 | 弱覆盖，无独立 n=20 交互档 |
