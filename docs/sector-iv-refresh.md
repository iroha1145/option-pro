# 板块隐含波动率的公开更新

板块隐含波动率（IV）使用独立后台任务 `sector_iv_refresh`，覆盖目录中的全部板块与成分股。选股前 20 名的每日快照仅作临时参考，不再阻止完整板块更新。

## 数据来源

使用现有雅虎财经（Yahoo/yfinance）期权链读取约 30 天到期的平值看涨期权 IV，保留已有期限选择、质量检查与回退规则；股票参考价格可由现有 Massive 股票快照补充。板块内分位由该板块有效样本排序得到，单个样本的分位为空。

供应商没有与本项目 24 个自定义板块对应的成分股平值 IV 排名。Yahoo 的板块接口只提供行业、概况及头部公司；Massive 的期权 IV 属于单独的 Options 产品，也以逐合约数据为单位。因此仍需匹配期限、行权价和板块成分，不能把普通行业涨跌幅或代理基金的 IV 当成成分股排名。

参考：[yfinance Sector](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Sector.html)、[yfinance Ticker 期权链](https://github.com/ranaroussi/yfinance/blob/1.5.1/yfinance/ticker.py#L42-L105)、[Massive Option Chain Snapshot](https://massive.com/docs/rest/options/snapshots/option-chain-snapshot)。

## 访问与更新

- `GET /api/sectors/{sector_id}/iv-ranking` 和 `heatmap` 读取共享快照，必要时登记有界的本地更新需求，不在请求内调用供应商。没有数据时返回空排名和更新状态，后台准备好后可读取结果。
- `POST /api/sectors/{sector_id}/iv-refresh` 对匿名、普通账号和管理员开放，不受 `visitor_live_pulls` 控制；只接受已存在的板块编号，要求同源操作，保留每客户端限流和每板块共享冷却。
- 更新状态放在 `refresh` 字段，包含 `status`、`retry_after_seconds`、请求/开始/完成时间、下次计划时间和错误代码。排队或执行中返回 202；冷却期间返回现有状态，不另起扫描。
- 页面在排队和执行时短间隔查询，完成后显示新结果。已有数据、部分数据及空状态都有更新入口；失败保留可用旧结果及原始时间。

同板块的重复需求在 SQLite 事务内合并，状态保存在 `sector-iv-snapshots-v1/refresh.sqlite`。后台定期覆盖全部目录，正常交易时段约每 15 分钟更新，其他时段约每 6 小时更新；主动请求遵守最短 300 秒间隔。请求会优先于纯计划任务，同一股票复用现有 Yahoo 缓存和并发保护。

## 数据时间与失败

数据新鲜度根据供应商结果的原始 `as_of` 判断，重新读取或保存不会把旧数据变新。最多保留 7 天内的旧数据供参考，超过期限的样本不再显示；未来或无效时间同样不可用。部分成功会保留有效成分并报告缺失列表，全部失败不会覆盖已有可用快照。

队列持久化冷却和失败退避，并使用执行租约避免重启后重复发布旧结果。长期排队或执行中断会转为明确的失败状态，允许后续恢复。后台任务状态和现有部署检查均包含 `sector_iv_refresh`。

修复验收覆盖匿名提交、重复请求合并、后台完成、快照落盘和重新读取，并检查单样本、超期旧数据、失败保留、市场时段和公开访问边界。旧部署升级不需要删除原快照或修改其他访客权限。
