# 当前状态

更新：2026-09-13 Round 1 代码已落地，实验室复测尚未完成。Goal **未**标记完成。

## 已完成

- 拉取并核对 `origin/main` = `31e8955d89dc2b9b51a5bea1c47f5cfa6ea8cabc`
- 分支 `cursor/perf-sitewide-1d7a`；对照工作区 `$HOME/option-pro-unoptimized`
- 全站矩阵初稿、冻结预算、环境诚实记录（4 核 / 15 GiB）
- 性能种子与测量脚本落在 `scripts/perf/`
- 实验室基线 n=20（mobile-ref 冷/热）
- Round 1：身份确认与 feed 解耦、非首屏请求 load+idle、feed 不再每次 `status()`

## 未完成（完成判定第 1–5 条均未满足）

- Round 1 同条件复测与交错对照
- feed 整窗物化 / 只对当前页 `_item()`（Round 2 候选）
- 桌面 / 360 / 430 / 弱网各 20 次；站内 SPA 导航
- 其它模块代表性任务与 >5% 退化复查
- 压力 / ≥2h 长稳 / 故障恢复
- 独立三轮复查
- 真机、Safari、日本到美国链路：**待验证，未执行**

## 受阻项

| 项 | 原因 | 剩余步骤 |
|----|------|----------|
| 按生产 16 核容量下结论 | 本 VM 4 核 / 15 GiB | 在报告中保持实验室标注；不改配置假装有 16 核 |
| 真机 iPhone Safari | 环境无真机 / 非 iOS WebKit | 交付可重复的 Chromium 移动档脚本 |
| 日本→美国 RTT | 无该链路权限 | CDP 180ms/300ms 实验室档 + 缺口说明 |
| Docker 双容器 | 无 Docker daemon | uvicorn 单进程对齐生产 CMD |
