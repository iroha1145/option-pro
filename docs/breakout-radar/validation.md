# 验证计划

## 测试原则

- Provider 测试完全离线，使用 httpx MockTransport 和固定 JSON。
- 测试全局封锁真实套接字，遗漏替身时立即失败。
- 时钟可注入，禁止真实 sleep 和本地时区依赖。
- 随机数据固定种子；核心量化场景使用显式 OHLCV。
- 每项测试清理缓存、熔断、限流和临时数据库状态。

## 覆盖矩阵

Provider：

- regular、premarket、空结果、列少/列多/重排、非数字。
- 正好达到响应上限、超一字节、错误 Content-Length 和分块超限。
- 429、5xx、连接/读取/总超时、stale-on-error、熔断、半开恢复。
- ticker 注入、价格大于 100、科技微盘无豁免。

时间与 RVOL：

- 未完成日线排除。
- 10:30 不读取 10:35 后数据。
- 盘前不读取 regular。
- 突破 K 线不生成自身阻力。
- 追加未来尾部后原 cutoff 结果不变。
- RVOL 只比较历史同一分钟；覆盖提前收盘、缺 K、伪零和样本不足。

基底、事件和生命周期：

- MOMENTUM_SPIKE 与真实结构突破分开。
- TRIGGERED 不越级到 CONFIRMED；两根保持可确认。
- HOLDING、RETESTING、RETEST_HELD、REACCELERATING、EXTENDED、FAILED。
- 高 chase risk 不降低 breakout quality。
- 重复扫描和 transition 幂等；同日新 pivot 新事件。

持久化与 Worker：

- 发布每一步注入失败，旧 completed 快照保持。
- Worker 重启、过期锁接管、第二 Worker 拒绝、旧 token 无法发布。
- WAL 下并发读取只看到旧完整或新完整快照。
- --once、SIGTERM、调度不漂移、Provider 故障不触发容器重启风暴。

适配与评分：

- sector filter、top、options coverage、候选集合不改变 intrinsic。
- market shape 只改 market_fit 和 priority。
- 缺失组件不补 50；活跃权重不足返回 null。
- contribution 合计与最终分误差不超过 0.1。
- 所有浮点有限，分数和置信度范围正确。

Range Persistence：

- Pine 离线金样覆盖预热、首个有效窗口、稳态、平坦和恢复。
- 逐步 exact 与广义式一致；附件化简与 M/N1 不变性只在 gate=1 稳态验证。
- legacy 可大于 100；改良值保持 0 至 100 或 null。
- 175 根预热、平坦 uninformative、cutoff 不变。
- shadow 不改生产分和排序，最终贡献上限 4%，缺失不产 50。
- candidate set 不改变规范化；非 canonical ticker 使用 self percentile。

API 和回归：

- GET 只读 completed scan，Provider 调用计数为零。
- 游标稳定且绑定 scan_run_id。
- raw Provider 不泄露，时间带时区，版本完整，降级状态明确。
- 现有 strength、signals、stocks、options、market 与 /ready 保持通过。
- 前端静态断言保持通过，且无视觉文件变化。

## 研究验证

使用滚动训练、验证、测试并向前滚动；重叠标签执行 purge 和 embargo。
保存 30 分钟、收盘、次日、5 日、20 日收益，以及 MFE、MAE、保持率、
假突破率、回踩成功率、失败时间和再加速率。

Range Persistence 比较 baseline、直接叠加和替换同类趋势权重。未达到
range-persistence.md 的启用门槛时始终保持 shadow。

## 完成命令

- focused range persistence pytest
- focused breakout pytest
- full pytest
- python compileall
- node static_assertions.mjs
- dependency source-hash verification
- pip check
- pip-audit
- docker compose config
- docker image build
- worker --once
- worker single-instance tests
- /ready、/api/breakouts/current、/api/breakouts/status 冒烟检查
- 旧 strength、market、signals、stocks、options 回归

失败命令必须记录原命令、退出码和原错误，并区分代码错误与环境限制。
