# Option Pro 与 MacroLens 远程集成架构

状态：冻结 v1  
冻结日期：2026-07-12  
Option Pro 起始提交：9a0ec5c591bc040ae7531c1c3e015b1c88b37fbb  
MacroLens 起始提交：a3c9e3fce76c96c0ff84303f566fa2b0e2afd1bb

## 职责

MacroLens 负责新闻抓取、去重、原始新闻保存、经济日历、GPT-5.6 Terra 分析、股票影响投影、分析任务和版本化远程接口。

Option Pro 负责远程签名客户端、本地持久缓存、独立同步进程、同源 Catalyst API、Night Desk 展示，以及突破雷达、选股、个股研究和财报页面的上下文接入。

浏览器只访问 Option Pro 同源路径。它不得获得 MacroLens 地址、服务密钥或 OpenAI Response ID。

## 固定边界

- 两个服务位于不同服务器。
- 不共享数据库、数据卷、Docker 网络或 Python 包。
- 所有跨服务器请求使用 HTTPS 和 HMAC-SHA256。
- Option Pro 普通 GET 只读本地 SQLite，不等待 MacroLens。
- MacroLens 故障不影响 Option Pro 的 /ready、行情、期权、强势、突破和财报数据。
- CATALYST_MODE 初始为 display；本阶段不得设为 enabled。
- RANGE_PERSISTENCE_MODE 保持 shadow。
- 催化剂不改变 intrinsic_score、ranking_score、breakout quality、alert priority、market shape 或生命周期。

## 数据流

新闻源 → MacroLens 去重和原始入库 → 持久分析任务 → Terra 结构化结果
→ 追加式分析版本和股票投影 → HTTPS Integration API
→ Option Pro Catalyst Sync Worker → /data/catalyst-cache.db 原子发布
→ 同源 Catalyst API → Night Desk。

## 进程

MacroLens：

- web：抓取、现有管理界面和本地接口。
- analysis-worker：领取持久任务，提交或恢复 OpenAI 响应，发布结果。

Option Pro：

- backend：核心 API 与只读 Catalyst API。
- catalyst-sync-worker：远程同步、远程任务代理和本地快照发布。
- ai-worker：Option Pro 自有财报及期权分析任务。
- breakout-worker：保持现有职责，不依赖 Catalyst。

## 文件所有权

MacroLens 持有 Integration v1 服务端模型、认证、nonce、任务、分析版本、日历版本和契约生成器。

Option Pro 固定契约副本，持有出站签名、远程客户端、本地缓存、同步进程、同源路由和 Night Desk 模块。两仓只复制 JSON Schema，不互相导入业务代码。

## 实现波次

1. 冻结文档、基线截图和契约。
2. MacroLens 模型运行时、任务、投影、认证和 Integration API。
3. Option Pro 客户端、缓存、同步进程和同源 API。
4. Option Pro 自有 AI 持久任务。
5. Catalyst Desk、分析抽屉和上下文接入。
6. 影子字段、回归、安全、容器和视觉验收。
7. 用户提供 MacroLens 服务器后完成真实 HTTPS、密钥、模型和故障联调。

## 向后兼容

旧 MacroLens 页面和旧分析读取保留；新分析不再保存隐藏推理。Option Pro 的旧财报 GET 只返回缓存或 analysis_required，不再创建付费任务。#detail/TICKER、现有五个页面、Search Palette 和 Breakout 数据库语义保持不变。
