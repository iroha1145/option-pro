# 计划

## 目标

在保留功能、数据正确性与时效、UI 信息完整性、安全性与部署兼容性的前提下，减少真实用户完成任务的等待与卡顿。优先新闻页与手机端，再覆盖其余前后台。

工作分支：`cursor/perf-sitewide-1d7a`  
基线 SHA：`31e8955d89dc2b9b51a5bea1c47f5cfa6ea8cabc`（已与 2026-09-13 拉取的 `origin/main` 对齐，不再追逐后续 main）  
对照树：`$HOME/option-pro-unoptimized` 同一 SHA

## 闭环

测量 → 定位 → 小批量修改 → 功能回归 → 同条件复测 → 保留或回滚。

优先级：功能与数据正确性 > 稳定性与安全性 > 用户感知性能 > 单项跑分。

## 已确认的高优先级调查点（修改前，待测量验证）

1. `GET /api/catalysts/feed` 先完整执行 `status()`，再把窗口内全部 revision 物化到内存后切片分页（`personal_service.py` + `local_intelligence.feed`）。
2. 新闻页首屏至少两次 feed：`StatusHero.newsToday`（24h / limit 50）与 `FeedPanel`（limit 12），另加 status / hotspots / focus。
3. 列表行使用 framer-motion stagger；Layout 全局 IndexTape + 身份确认后才挂页面。
4. 生产镜像为单进程 uvicorn，无 `--workers`。本虚拟机 4 核 / 15 GiB，不能按 16 核 / 32 GiB 宣称容量。

## 不在本轮做的事

- 不访问生产库、不部署、不向 `main` 推送、不合并 PR。
- 不引入 Redis / 消息队列 / 框架重写。
- 不缩减可访问新闻、历史范围、刷新频率或精度来换指标。
