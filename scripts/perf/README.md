# 性能测量脚本

本目录可单独运行，不进入普通 CI。重型压测不要塞进每次 `pytest`。

| 脚本 | 作用 |
|------|------|
| `seed_catalyst_news.py` | 固定种子写入 `catalyst-cache.db`（100 / 1000 / 10000） |
| `measure_api.py` | 对真实应用 + 真实测试库打 HTTP，记录 p50/p95/p99 与正确性 |
| `measure_browser.mjs` | 生产页 Chromium 实验室计时（LCP/CLS/`news_content_ready`） |
| `measure_feed_inprocess.py` | 进程内 `PersonalCatalystService.feed` 计时（冷/热 revision 缓存） |
| `run_isolated_backend.sh` | 单进程 uvicorn，`DATA_DIR` 指向种子目录 |
| `measure_interact.mjs` | 新闻页抽屉 / 筛选 / 滚动（实验室，不是真实 INP） |
| `measure_pages.mjs` | 其它路由首屏内容可见计时 |
| `measure_load.py` | 到达率混合 URL 探索性压测（勿打生产） |
| `run_soak.py` | 默认可跑 2 小时的隔离长稳；不进普通 CI |
| `measure_spa.mjs` | 同页站内回到 /catalysts（不是整页热缓存） |
| `measure_interleaved.mjs` | 优化树与未优化树交错冷/热（禁止冷比热） |
| `run_faults.mjs` | 新闻页断网 / 429 / 慢响应 / 清缓存后恢复（路由拦截，不打付费上游） |
| `run_restart_recovery.py` | 重启隔离后端后对照 feed 条数与首条标题 |
| `watch_rss.py` | 采样 uvicorn RSS，供长稳区分有界增长与泄漏 |
| `analyze_soak.py` | 长稳 JSONL 早/晚 p95 与 RSS 摘要 |
| `run_post_soak_suite.sh` | 等 2h soak 摘要后重建 frontend 并跑视口 / SPA / 页 / 交错 / 故障 |
| `run_remaining_suite.sh` | 视口 n=20 之后续跑 SPA / 页 / 交互 / 交错 / 故障（不重建 frontend） |
| `run_final_regression.sh` | 剩余套件结束后的最终关键回归：前端测试 + 催化 pytest + mobile-ref/交互 n=20 |
| `lib/rate_limit.mjs` | 测量脚本共用：对间间隔 + 遇到 429 冷却 60s（不放宽产品限流） |

原始结果默认写到 `/opt/cursor/artifacts/perf/`。体量可控的复测 JSON 同时放入 `docs/performance/artifacts/`，避免只留 Cursor 机器绝对路径。
