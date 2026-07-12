# 时间点一致性

## 三个时间

- published_at：来源发布时间。
- fetched_at：MacroLens 首次看到新闻的时间。
- analyzed_at：模型结果通过校验并提交的时间。

分析可用时间固定为 available_at = max(fetched_at, analyzed_at)。

经济日历版本的 available_at 不早于上游响应实际接收和入库的时间。请求开始时刻不能作为可用时刻；使用旧缓存降级时，也要等失败判定完成后才发布 stale 版本。

## 查询条件

历史查询必须满足：

~~~text
published_at <= as_of
fetched_at <= as_of
analysis.available_at <= as_of
~~~

published_at 缺失时仍要求 fetched_at <= as_of。未满足分析条件时，新闻可以存在，但 analysis、analyzed_at 和 available_at 均为 null；不得派生方向、影响分或置信度。

## 示例

新闻 10:00 发布，10:04 抓取，10:06 完成分析。10:05 的突破回放可以显示原始新闻，但不能显示 10:06 的方向、分数或置信度。

## 版本

分析强制重跑产生追加版本。日历 Forecast、Previous、Actual 的后到更新也产生追加版本。查询选择 available_at <= as_of 的最近版本，不能把最新行覆盖回过去。

## 游标

Feed 游标冻结 as_of、筛选摘要和排序键。Option Pro 本地分页还固定 read_cutoff_at，避免翻页期间新同步的旧新闻插入已读结果。

## 影子研究

影子字段只使用当时可见的记录，同一 content_hash 只计算一次。没有记录时为 null，不补 0 或 50。任何影子输出不得改写正式评分或历史事件。
