# 性能测量脚本

本目录可单独运行，不进入普通 CI。重型压测不要塞进每次 `pytest`。

| 脚本 | 作用 |
|------|------|
| `seed_catalyst_news.py` | 固定种子写入 `catalyst-cache.db`（100 / 1000 / 10000） |
| `measure_api.py` | 对真实应用 + 真实测试库打 HTTP，记录 p50/p95/p99 与正确性 |
| `measure_browser.mjs` | 生产页 Chromium 实验室计时（LCP/CLS/`news_content_ready`） |
| `measure_feed_inprocess.py` | 进程内 `PersonalCatalystService.feed` 计时（冷/热 revision 缓存） |
| `run_isolated_backend.sh` | 单进程 uvicorn，`DATA_DIR` 指向种子目录 |

原始结果默认写到 `/opt/cursor/artifacts/perf/`。
