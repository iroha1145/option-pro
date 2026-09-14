# PR #162 审查修复（第二轮：M1–M3 / L1–L5 / nit）

修复基准 `16a389667e8ddd04a15a4e3f5cb4fc92b6ff7e2e`，对应审查稿的编号。本轮不重新测量性能，也不把实验室数字当生产验证。

## 修复内容

- **M1 抽屉关闭滑出期间保留最后一条**：`item` 由 `newsId` 派生后关闭即为 null，而 Drawer 还要滑出 350ms，内容闪成骨架屏。现在展示值回落到最后一条（`lastShown` 只做展示；提交、轮询、恢复仍看 `newsId` 与 `openNewsRef`）。关闭不再清错误/提示文案，换条时才清。
- **M2 AI 能力占位不再当作「关闭」**：`useAccess` 暴露 `aiPending`（owner 已确认、/ai/status 与运行设置未回）。`ImpactCard` 在 pending 期间不发第一次报告级 GET，硬重置键纳入 `aiPending`，能力落地后只读一次；Navbar AI 徽标与财报页头在 pending 时显示「确认中」而不是「未开启」。
- **M3 自动重试等待封顶**：`runBoundedRead` 新增 `MAX_AUTO_RETRY_WAIT_MS = 10s`。Retry-After 超过上限直接抛错，界面立即显示「详情更新失败 / 重试」（有 seed）或「详情不可用 + 重试」（无 seed，错误态新增重试钮）；上限内仍按 max(本地退避, Retry-After) 睡。
- **L1 theme-boot 预取 promise 挂旁路 `catch`**：主包接管前的拒绝不再是 Uncaught (in promise) / pageerror；`await` 原 promise 仍拿到同一拒绝。
- **L2 悬停/展开预取按当前筛选构造 URL**：`feedApiPath` 成为 feed 路径唯一来源，FeedPanel 页大小与预取同源；带筛选时不再预取一个必然未命中的默认 URL。
- **L3 今日计数不再固定等 3.5s**：feed 首页落地（成功或失败）即拉取，load 后 3.5s 只是兜底；站内切换命中缓存时立即显示。
- **L4 用户刷新不跳过服务端 Retry-After**：`ResourceCache` 记录 `serverRetryAt`，用户刷新只清本地退避。
- **L5 在 seed 上提交分析后补读详情**：读取绑定新任务 id；合并不降级任务态（服务端投影未带上新任务时保留提交态）。
- **nit**：`perf-regression-gate` 只钉实验室脚本语义，去掉对产品源码数值/标识符的文本正则；FilterBar 预取副作用移出 state updater；任务恢复读到 404 显示「任务记录已不存在」且不给必然失败的重试钮。

## 回归覆盖

新增/更新用例：Retry-After 超上限立即报错与手动重试、上限内 5s 自动重发、无 seed 错误态可重试、关抽屉滑出期间保留内容且换条回骨架、404 不给重试、seed 上提交后补读（两种服务端投影）、关闭取消上限内的重试睡眠、服务端 Retry-After 不被用户刷新跳过、`aiPending` 语义、theme-boot 旁路 catch。

## 验证

- 前端行为测试 988/988 通过，0 跳过
- `tsc -b --force` / `eslint .` 0 错
- `VITE_API_MODE=live` 构建后 `frontend/` 与 `frontend-src/dist` 逐字节一致；`static_assertions` 通过
- `git diff --check` 干净
- 后端无改动；未重新测量实验室性能

## 发布前独立复核

另修复两处提示状态：seed 提交后的详情补读成功时清除旧错误；财报卡片首次已有标的时显示加载，不再误提示选择标的。新增三项定向回归，最终前端行为测试 **991/991 通过、0 跳过**；最终构建、静态检查和产物一致性检查通过。原 Claude 工作区保持不动，修复在独立发布目录整合。
